# 灵魂引擎

> `SoulEngine` 接受 runtime-owned gate，内部服务与主服务共享同一对象；`SocraticDialogue` 优先复用 Soul 服务。Prompt、token 与成本语义未变。

> 用户深度理解核心 — 从行为数据到人格画像的推理引擎。

## 概述

`soul/` 包实现了用户理解的核心逻辑，包括：

- **SoulEngine** — 编排器，从事件出发驱动各层分析
- **PreferenceAnalyzer** — LLM 驱动的偏好提取和合并
- **AwarenessAnalyzer** — 基于近期事件生成结构化觉察笔记
- **InsightAnalyzer** — 基于觉察、偏好和画像生成洞察假设；合并同名假设时按 `user_verdict` 决定置信度走向（见下「假设置信度与用户判断」）
- **DialogueInsightAnalyzer** — 从聊天中提取候选长期理解信号
- **ToneProfile** — 从画像、偏好和近期反馈推断语气风格，用于推荐、画像总结和对话
- **SocraticDialogue** — 苏格拉底式用户对话，通过追问深化理解
- **AvoidanceSpeculator** — 主动确认用户可能想避开的内容方向
- **SoulProfile** — 用户灵魂画像数据结构

> **画像 → LLM 的所有序列化路径**（哪些 prompt 看到画像、看到哪些字段、是否含
> `personality_portrait`）统一登记在 [画像使用登记表](../profile-usage.md)。新增
> 消费画像的 prompt 前先查这张表，并复用其中的 view，不要另造序列化分支。

## 有效 dislike 的即时可见性（2026-08-07）

`SoulEngine.get_effective_disliked_topics()` 合并用户 overrides 后的 Soul dislike 树与 flat
`preference.disliked_topics`，并让 domain/specific remove 最终生效。对话学习或显式编辑先写 flat preference 时，
`get_profile()` 会把这份权威快照立即覆盖到返回的 `OnionProfile.interest.dislikes`；调用方不必等待完整 Soul rebuild。

这份即时画像供 discovery evaluator 和 RecommendationEngine 使用。异步 exact/semantic pool purge 仍负责清理已有
库存，但普通 dislike 不撤销关键词或抓取任务；推荐历史缓存与最终展示出口另行读取同一权威快照保证即时一致性。

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| OnionProfile 五层重构 | ✅ | 将 SoulProfile 重构为五层洋葱模型（CoreLayer → ValuesLayer → InterestLayer → RoleLayer → SurfaceLayer） |
| MBTI 人格类型 | ✅ | Core 层新增 MBTI 类型与维度强度（E/I, S/N, T/F, J/P），支持置信度标注 |
| 树形兴趣结构 | ✅ | InterestLayer 改为领域树结构 (domain → specifics)，支持”国际时事 → 中东局势 / 欧洲政治”的多层级兴趣 |
| 双存储（JSON + Markdown） | ✅ | soul_profile.json 存储结构化数据，soul_profile.md 提供人类可读镜像 |
| 画像变更日志 | ✅ | 新增 soul_changelog.md 记录每次画像更新的时间、来源、变化摘要和影响 |
| 向后兼容垫片属性 | ✅ | OnionProfile 提供 `core_traits / deep_needs / cognitive_style / motivational_drivers / values` 等垫片属性，兼容旧代码渐进迁移 |
| 自动格式迁移 | ✅ | `from_legacy()` 支持将 v1 flat SoulProfile 自动迁移到 v2 OnionProfile，SoulEngine 透明处理版本升级 |
| SoulEngine.analyze_events() | ✅ | 事件 → PreferenceAnalyzer → 偏好层更新；v0.3.162+ 新增可选 `progress_callback: Callable[[int, int], Awaitable[None]]`（透传给 `PreferenceAnalyzer.analyze_events`），分片路径每完成一个 chunk 回调一次 `(done, total)`（并发 gather 下 done 仍严格递增）、单发路径回调一次 `(1, 1)`；回调异常吞掉 log WARNING，观测者绝不影响分析结果，也不触碰任何 prompt 构造 / 分片方式 / 序列化（prompt-cache 约定不变）。guided init 阶段 2 用它驱动 GUI 分片进度与 CLI 逐批打印 |
| SoulEngine module overrides | ✅ | 构造时可接收 `module_overrides` 并注入内部 `LLMService`，确保 preference / awareness / insight / profile_builder / speculator / dialogue_insight 都遵循 `[llm.soul]` 路由 |
| Task-scoped cognition prompt rollout（2026-08-06） | ✅ | `SoulEngine` 分别接收 `preference_prompt_view` / `awareness_prompt_view` / `insight_prompt_view`；默认 `legacy / compact-v1 / legacy`。SenseTime 真回放实际覆盖的是 `build_awareness_with_confusions_prompt` / `soul.awareness_confusions`，所以 AwarenessAnalyzer 明确拆成两个 seam：普通 `analyze()` / `soul.awareness` 固定 `legacy`，只有 `analyze_with_confusions()` 获得 `awareness_prompt_view`。不存在聚合 full-compact 构造参数；replay 的显式双臂不受生产默认影响，artifact 用任务名 `awareness_confusions` 避免误称整个 Awareness 已放行 |
| PreferenceAnalyzer | ✅ | LLM structured extraction + 合并 + 衰减；偏好分析 system prompt 注入 `CATEGORY_VOCAB`（静态常量、缓存安全），代码侧在 `(name, category)` 合并键生成前执行 `resolve_category()`：词表外 → embedding 最近邻（≥0.55）→「其他」，任何路径都不会把词表外一级分类写入 preference 层；v0.3.x `satisfaction_filter_enabled=True` 默认开启，构 prompt 前会丢掉 `quick_exit` 等被动 negative 事件，保留 positive + neutral + unknown / NULL；显式 `dislike` / `thumbs_down` 负反馈会保留为 disliked_topics / 风格避让证据；偏好分析调用前有 prompt 预算保护，超长 chunk 会递归二分，单条超长事件会 compact，`n_keep >= n_ctx` / `context length` 等上下文错误会用更小 chunk 重试；chunked 分析遇到 LLM 拒答 / 非 JSON 时会对单条事件追加 title / URL / source-only 安全压缩重试，避免长网页 context 触发安全拒答后直接丢失该条画像信号；偏好归一化对 LLM 输出做 schema 校验（`_normalize_style` / `_normalize_context_dict` / `_finalize_taste`）——`preferred_duration`(short/medium/long) / `preferred_pace`(fast/moderate/slow) 越界重置为 ""、非数值口味字段与 `exploration_openness` 回落字段默认 0.5（合法字面 0 保留）、数值 clamp 到 [0,1]、context 占位符（unknown/none/n/a/未知）清空，任一字段被纠偏即打一行列出字段名的 WARNING（避免画像面板静默全 unknown/0%）|
| Init 认知草稿落库（2026-07-26+） | ✅ | `analyze_events` 在偏好落盘后调 `_persist_init_cognition_drafts`，把 `_init_cognition_context` 的觉察/洞察经与常规认知**同一条 merge 路径**写入 `awareness.json` / `insight.json`（去重、生命周期、`user_verdict` 语义一致）。觉察挂本轮 init 记入事件账本的真实 event id（上限 `_INIT_DRAFT_EVIDENCE_CAP=300`）并标 `source_event_ids_approximate=True`——模型是按轮归属而非按条，不假装精确。洞察一律 `validated=False` / `user_verdict=""`（是待确认的假设，不是结论）。落库记 `init_cognition_persist` 台账；整段 best-effort，失败只 WARNING 不影响 init。**动机**：草稿此前只影响首份画像随即丢弃，加上 init 历史当时不入事件表，认知循环连重新提炼的素材都没有，导致全新装机后「待聊确认」是空的 |
| 收藏作为独立行进画像（2026-07-26+） | ✅ | `build_initial_profile` 收到的 `combined_history` 里每条收藏各自成行（`event_type="favorite"`），不再塌成 `[收藏夹汇总]` 一行。`event_type` 承重：强信号权重 3.0 + 采样 40% 预留份额 + 语境渲染成「收藏了」；`_history_timestamp` 读 `fav_time` 让收藏参与时间分层。**动机**：旧实现里 `_favorites` 列表写入了却无人读取，`_summarize_history` 只取汇总句，于是用户主动收藏的内容在画像里一个标题都不可见 |
| Init chunk cognition context | ✅ | 初始化偏好分片可顺带输出 `awareness_candidates` / `insight_candidates`；**多分片合并按轮转分配（2026-07-26+）**：分片是 `events[i:i+200]` 且拉取顺序最新在前，旧实现按分片顺序遍历、到 cap（觉察 12 / 洞察 8）即 `break`，结果最近的一两个分片吃光配额、更早时期一条都进不去（实测三时期各 200 事件：最早期觉察 0 条、洞察 0 条）。现改为**从时间两端交替**（最新、最早、次新、次早…）每轮各取一条、去重后进入下一轮——纯按分片顺序轮转在分片数量倾斜时仍然偏向最近（一次刷屏就能占掉大部分分片，真机实测洞察里的早期内容反而归零），两端交替保证最早的分片在预算内一定被访问到；产出少的分片自然退出后续轮次而不占用名额；cap 与去重语义不变（同场景修复后为每时期各 4 条）。与 `ProfileBuilder` 的历史抽样是同一类「先到先得＝最近独占」问题；`PreferenceAnalyzer` 去重合并为私有 `_init_cognition_context`，`SoulEngine` 只在紧接着的 `build_initial_profile()` 中作为 prompt 上下文消费，不写入长期 `preference.json`、`awareness.json` 或 `insight.json` |
| 初始化分片可观测性 | ✅ | `PreferenceAnalyzer` 为每个并发分片记录带序号的 started / done / failed / cancelled 生命周期和墙钟耗时；guided init 同时把严格递增的完成数回调给 CLI/API。日志只增强定位能力，不改变分片并发、失败传播、超时取消或偏好合并语义 |
| filter_events_by_satisfaction | ✅ | `soul/event_filters.py` 中的纯函数，按 `inferred_satisfaction` 过滤事件，`"unknown"` 同时匹配缺失 / `None`，使 pre-migration 老行可被显式 opt-in 保留 |
| recent_negative_exemplars | ✅ | `soul/negative_exemplars.py` 中的纯函数，从事件层拉最近 negative 标题做 recency 加权（半衰期默认 14d）+ 前缀去重 + 80 字截断，最多返回 16 条 `{title, reason, age_days}`。下游消费者是 `discovery/engine.ContentDiscoveryEngine._evaluate_batch` 和 `recommendation/engine.RecommendationEngine._classify_batch`，二者都会把列表作为 `negative_examples` 透传给 batch evaluator prompt——这是 [inferred_satisfaction 信号](#) 的第二个消费方（第一个是上面的 `filter_events_by_satisfaction`） |
| SocraticDialogue.respond() | ✅ | 通过 LLMService 调用 LLM，自动注入画像；同一 dialogue 实例逐轮串行执行普通与工具调用，用户 turn 在真实回复完成前仅为临时历史，异常/取消只回滚本轮且不触发学习 |
| ProfileBuilder 历史抽样（2026-07-26+） | ✅ | `_summarize_history` 不再按到达顺序切`titles[:100]` / `contexts[:100]` / `recent|older[:50]`——真实拉取顺序是最新在前，1000 条历史里模型只看得到最近约 100 条，再久的长期兴趣无论互动多强都不可见（实测生产数据：旧法只覆盖**最近 0.6 天**，且漏掉了全量里唯一一条收藏）。现按「强信号保底 + 时间分层」抽样，与增量链路同源判据：① 权重复用满意度语义——明确互动（收藏/点赞/投币…）3.0 > 高完播 2.0 > 一般 1.0 > 划走 0.3（不归零，划走也是信号）；② 先用 `_HISTORY_STRONG_RESERVE=0.4` 的预算无条件收下明确互动（避免一段时间内集中的收藏被其他时间桶的配额挤掉，与「疑惑被高置信假设埋掉」同类问题），余额再按 `_HISTORY_TIME_BUCKETS=6` 个时间桶均摊，薄桶剩余配额回流给最有代表性的行为；③ 输出按时间排序，`count` 仍报真实总量并附 `sampling_hint` 告知模型这是抽样。无有效时间戳（超过半数缺失）时退回到达顺序，不丢数据。**未改动**：`analyze_events` 的偏好分片仍是 `events[i:i+200]` 全量覆盖，init 的觉察/洞察（`_init_cognition_context`）也无截断——截断问题只存在于画像构建的历史摘要这一处 |
| ProfileBuilder | ✅ | 结构化 prompt + JSON 校验 + `OnionProfile` 构建；`build_soul_profile_prompt()` 的 system prompt 保持静态，user prompt 按 `<tone_profile>` → `<preference_summary>` → `<recent_awareness>` → `<active_insights>` → `<history_summary>` 排列并使用确定性 JSON，让超大的历史摘要位于 provider cache 前缀末端 |
| SoulEngine.build_initial_profile() | ✅ | 从 history + preference 生成并持久化 `soul.json` |
| SoulEngine.get_profile() | ✅ | 从 soul 层读取画像并叠加用户覆盖层返回**有效画像**，未初始化时抛明确异常 |
| SoulEngine.get_raw_profile() / get_overrides() | ✅ | 返回不叠加覆盖的纯 AI 画像 / 当前 `ProfileOverrides`，供编辑态与 AI 漂移比对 |
| 用户画像覆盖层 (`soul/overrides.py`) | ✅ | `ProfileOverrides` + 纯函数 `apply_overrides`（文本/标量固定、列表增删、兴趣树 domain 增删/权重与 specifics 增删）+ 带校验的 `apply_edit` 归约器 + `build_edit_state`；兴趣二级项通过 `/api/profile/edit` 的 `parent` 字段定位父 domain，`edit-state` 同步暴露 `specific_edits` 供三端编辑 UI 标注；同一二级项新增后再删除会归约为空覆盖，避免留下伪编辑痕迹；用户手动编辑存独立 `profile_overrides.json`，读时叠加到 AI 画像之上，画像重建不覆盖；列表 remove 持续抑制 AI 再次推断出的同项 |
| 分类词表 + 一次性迁移 | ✅ | `soul/taxonomy.py` 定义 19 项固定一级分类词表 `CATEGORY_VOCAB`（含「其他」，代码常量非 config），`resolve_category()` 按精确命中 → embedding 最近邻（≥0.55）→「其他」解析；`CategoryMigrator` 用一次 LLM 映射把存量自由分类迁移到词表，代码校验完整覆盖且目标必须在词表内，失败零写入；应用前写 `consolidation_runs/<run_id>.json`（`kind=category_migration`）并追加 `soul_changelog.md`，复用 `profile-consolidate --revert` 回滚 |
| ProfileConsolidator（12h 画像整理） | ✅ | LLM 整理合并重复的喜欢 / 讨厌主题：规则层同名同类合并（零成本）；同名异类构造独占的强制嫌疑簇送 LLM 裁决（同名异义防护，no-merge 用 `name::category` 限定键）→ likes 以 embedding + 词面重叠构造相似图并取连通分量，不再用“首成员命中即占用”的贪心分组；默认跨类候选阈值 ≥0.80、同一 category 的二级兴趣再放宽 0.04，超库存按水位压力最低到 0.72，dislikes 保持严格 ≥0.85。跨 category 的词面召回只接受包含关系，避免“游戏资讯 / 科技资讯”靠通用后缀串成大簇；无 embedding 仍可走同一保守词面图。no-merge 会切断已判 distinct 的边但不遮住成员的新邻居，且以 `known_distinct_pairs` 进入 prompt 与代码校验，禁止经传递路径重新合并；策略版本升级只清理旧“严格同义词”口径的模型 keep，用户显式 revert 的 pair 单独保护，旧状态也可从 run snapshot + changelog 恢复。分批 LLM（每批 32 簇）按“是否重复占用同一推荐意图”输出 merge/keep：像“搞笑 / 娱乐搞笑”可合并，真正改变召回范围的父子兴趣仍保留；代码继续校验 members 逐字存在、簇内全覆盖、canonical 禁裸大词与避雷严禁向上泛化。单批失败不阻断其它批，但只要存在失败 / 缺失 / 非法响应就不写 clean digest，下一 due tick 会重试相同输入；完成日志带 `retry_pending`。LLM canonical 优先选能覆盖整组的简洁旧 member，写回时保留原词到 `aliases`，后续增量命中 alias 会强化 canonical。覆盖范围默认为 likes 权重 top-512 + 全量避雷；active likes 超过 `profile_consolidation_like_target_upper` 时临时开 full boundary，合并后仍超上限则把低权重且非用户保护的长尾移入 `archived_interests`，新信号可复活；`profile-consolidate --full` 仍可手动全量整理。embedding + LLM 窗口结束、真正写入前会对 active / archived / dislikes 做完整 revision 校验；若 preference analyzer 同期落入新证据，本轮零写入、零状态推进并让下一 tick 重试，避免旧快照覆盖新兴趣。改 flat preference 后经 `populate_from_flat_preference` 重建 Onion 树，且先 remap `profile_overrides.json` 再刷新有效画像镜像；应用记录在 `consolidation_runs/<run_id>.json`，同时备份原始 flat preference、完整 raw `soul.json` 与被改动的 overrides，再追加 `soul_changelog.md`；新记录的 `revert(run_id)` 会精确恢复原始 Soul 树及有效画像镜像，旧记录仍兼容按 flat preference 重建，并固定被回滚 pair。由 pipeline tick 调度（默认 12h），应用后发 `profile_consolidation` 认知更新卡片 |
| 画像整理成员引用兼容（2026-09-16） | ✅ | 普通 likes 整理簇同时接受字符串和 `{name, category}` 成员引用，避免模型按输入对象原样返回时被误判为 unknown member；同名异类簇仍强制使用 `name::category` 分类限定键。 |
| SoulEngine.get_effective_disliked_topics() | ✅ | base（raw soul.interest.dislikes ∪ raw preference.disliked_topics）再套覆盖层 remove/add（remove 最后生效），供推荐 / delight 最终过滤，用户移除项不被 raw 反向打穿；`get_profile()` 会在 Soul 重建前把该快照覆盖进有效画像 |
| SoulEngine.apply_user_edit() | ✅ | 折叠一次确定性编辑：存覆盖层 → 同步正向/避雷两套 speculator → 记 `source=manual` cognition → 重渲染有效画像镜像并通知两端 → 新增 dislike 按编辑前后差集把 `purge_pool_for_new_dislikes` 清池**调度为 `asyncio` 后台 detached 任务**（embedding 召回 + LLM 分类耗时数十秒，绝不能阻塞编辑响应，否则前端看着像「加了没保存」；`_schedule_dislike_purge` 派发，`wait_for_pending_edits()` 供测试 / 优雅关闭等待） |
| AwarenessAnalyzer | ✅ | 近期事件 → `AwarenessNote` 列表，支持同日去重；解析 LLM 响应时复用 `llm.json_utils.extract_llm_json_list()`，兼容 `results/items/notes/data/observations/recent_observations/latest/latest_observations` 等 object-wrapped array、reasoning 模型 bare singular-note dict、wrapper-key 下单 note、fenced JSON、JSONL 和 MiMo malformed `{ [ ... ] }`；prompt 按画像 → 偏好 → 近期事件排序以保留缓存前缀，并把近期 `dislike` / `thumbs_down` / negative 事件视为“最近开始避开 X”的保守观察信号 |
| InsightAnalyzer | ✅ | 觉察 + 偏好 + 画像 → `InsightHypothesis` 列表，支持假设合并；解析 LLM 响应时复用共享 JSON helper，能兼容 object wrapper、schema echo 后最终结果和 MiMo malformed array root |
| CognitionCycle | ✅ | 半日节流生成 awareness + insight 并同步到 `OnionProfile`；仅在 preference 与 soul 都为空的早期初始化状态跳过，已有任一层时仍会运行，避免已初始化画像因 preference 暂空而长期不产出觉察；awareness 失败时单次重试（间隔 2s），仍失败则记 WARNING 且**不推进** `last_awareness_at`，下一 tick 立即重试而不是空等 12h |
| CognitionCycle 游标增量取数 | ✅ | 觉察/洞察改**内容游标 + 大批量**取数，取代旧固定窗口（觉察曾 `query_events(limit=50)`、洞察曾全量读觉察）。觉察按 `last_awareness_event_id`（写进 `cognition_cycle_state.json`）只读 `id > 水位` 的事件，无新事件即跳过不调 LLM；单批容量 `_AWARENESS_EVENT_BATCH_SIZE=300`（按 256k+ 长上下文模型设计，~100 token/事件，正常 12h 窗口单次调用即可，**不为几十个事件强行分批**），仅积压超 300 才分批、作为防超大积压的安全网；每批成功后**逐批推进水位**（中途失败不丢已处理批），首批附 10 条已处理事件作趋势上下文；积压超 `_AWARENESS_BACKLOG_CAP=900` 时水位跳到最新窗口并记 WARNING（不静默丢）。洞察按 `last_insight_awareness_index`（觉察 append-only 的位置游标）只读新觉察、单批 `_INSIGHT_NOTE_BATCH_SIZE=150`（cap 450），并把当前活跃假设作 `existing_hypotheses` 上下文透传（`build_insight_prompt` 新增形参，system 仍静态、缓存不破）。批量 LLM 调用用更大的 `_COGNITION_MAX_TOKENS=32768`，两个 analyzer 的 `analyze()` 新增 `max_tokens` 形参。issue #169 起，`_AWARENESS_EVENT_BATCH_SIZE` / `_INSIGHT_NOTE_BATCH_SIZE` / `_COGNITION_MAX_TOKENS` 三处常量可通过 `[soul] awareness_event_batch_size` / `insight_note_batch_size` / `cognition_max_tokens` 配置覆盖（默认值仍为 300 / 150 / 32768），适配 80-100K 上下文的本地模型（如 qwen3.8-27B） |
| SoulEngine.generate_awareness_note() | ✅ | 生成并持久化 `awareness.json` |
| SoulEngine.generate_insight() | ✅ | 生成并持久化 `insight.json` |
| SoulEngine.update_from_feedback() | ✅ | compatibility facade 仍按 feedback event → 假设对象 → rebuild marker 的历史顺序工作；三段分别提取为 `apply_feedback_object()`（confirm→validated+置信度≥0.75，reject→未验证+≤0.35）、`mark_feedback_rebuild()` 与只读 `feedback_result()`。对话结算公开 admission façade `submit_hypothesis_settlement()` / `submit_confusion_answer_settlement()` / `submit_confusion_settlement()` 只构造 immutable payload 并等待唯一 queue worker；仅实际 worker Task 可调用 `_apply_*`。内部层先校验受理时冻结的 `AnchorAdmissionSnapshot`，再读取/创建 immutable ref winner。旧 `settle_*` direct executor、执行期 current-anchor 补抓、claim/lease/segment CAS 与恢复 scanner 均已删除；`applied=1` 才发布对象与全端投影。卡片四动作、legacy、锚建立/释放/恢复、普通 chat settles、探针与疑惑归属重放均已接入同一队列。 |
| SoulEngine.process_feedback_batch_if_needed() | ✅ | **默认是统一兴趣线的 durable owner**：`scheduler.unified_interest_line=true` 时持续读取 pipeline checkpoint 的 feedback cursor 之后的新行，只把显式 `like/dislike/comment/dismiss` 且非 import 的内容反馈转成稳定 `feedback-event-{row_id}` 信号；`checkpointed_enqueue_batch()` 将 buffer+cursor 原子发布到同一 `pipeline_state.json`，再用 `tick_if_buffered()` 消费。hypothesis/import 行跳过但推进 cursor，retraction 保留普通行为折价路径。v0.3.191 升级首次先用 owner-v2 cutover fence 跳过旧 direct-owned 尾部，边界按最大 row id 而非 `created_at`；`feedback_state.json` 只作兼容 provenance。`/api/feedback` 只落账并唤醒 scheduler，不等待 pipeline/LLM；启动时也主动恢复一次。`false` 才回到旧批线。三个调用方接口不变 |
| SoulEngine.record_immediate_feedback_cognition() | ✅ | 单条 `dislike/comment` 可即时写入结构化 cognition card，供插件画像页展示；评论类更新会带上对应内容标题，并以中性直接反馈记录，不预设正负向 |
| 卡片反馈纠偏边界 | ✅ | 卡片 like/dislike 是可撤销的软信号并由后台批处理学习；需要确定性修正时，用户仍可主动前往原有画像页写入持久 override，或在原有对话页用自由文本说明偏好；推荐区不新增纠偏引导入口。单次 dislike 不会直接永久屏蔽主题 |
| DialogueInsightAnalyzer | ✅ | 从聊天轮次提取 `goal/value/interest/dislike/state` 候选信号 |
| SoulEngine.learn_from_dialogue() | ✅ | 聊天落 `dialogue` 事件、累计 insight candidate；单条 `interest/value/goal/dislike` 聊天信号到中高置信度时会先写入轻量 cognition update，高置信度或重复出现达阈值后再驱动偏好/画像更新。`SocraticDialogue` 派发这条用户主动学习链时使用 task-local background-admission bypass：空库存或后台 LLM 暂停不会把 `soul.dialogue_insight` 永久 park，但所有 provider 调用仍经过 total gate。若本轮真正新增 `disliked_topics`，偏好落盘后会立即按新旧差集调度共享 `purge_pool_for_new_dislikes`：精确清池先执行，embedding + LLM 精判与完整画像重建并行；行为与手动画像编辑、反馈批处理和避雷探针一致，且不阻塞对话回复。对话 prompt 会如实区分本地长期画像/推荐过滤与平台自身推荐算法 |
| 画像更新台账（`soul/ledger.py`，v0.3.174+） | ✅ | `ProfileLedger` 是画像写点的**只追加审计观察者**：动作结束后一次 `INSERT` 到 `profile_update_ledger`，行含 `outcome(success\|failed)`、before/after 摘要、`diff`（top-level changed keys，≤2000 字符）、`source_refs`、`turn_id`，以及为后续 Wave 预留的 `gate_verdict` / `held_id`。普通写点保持空 `effect_key`；结算主台账和 revise-derived 台账分别使用 ref/content hash 稳定 key，retry 通过 `INSERT OR IGNORE` 补缺且不重复。台账始终是 **best-effort**：写失败只记 WARNING，不阻断业务对象或 `applied=1`；applied receipt 的显式 retry 会再次尝试缺失 observer。`action()` 上下文管理器行为不变。枚举写点见下「[画像写点台账挂钩清单](#画像写点台账挂钩清单)」。CLI 查询：`openbiliclaw ledger [--line] [--days] [--write-point]` |
| 觉察证据链（`AwarenessNote.note_id / source_event_ids`，v0.3.174+；逐条归属 2026-07-26+） | ✅ | `AwarenessNote` 带生成式 `note_id`（uuid hex 前 12）、`source_event_ids` 与 `source_event_ids_approximate`。**优先逐条归属**：awareness prompt 要求每条 note 给出自己依据的事件 id，解析侧校验其必须是本轮实际投喂批次的子集——只要出现越界 id（模型编造）就整条降级回整批归属并记 WARNING，宁可诚实近似也不要指向从未参与的事件。只有通过校验的逐条引用才是 `approximate=False`；模型给空数组或校验失败时，整批 id 挂到该 note 并标 `approximate=True`。因此消费方可以区分「这几个事件产生了这条观察」与「这条观察出自这批事件中的某处」。 |
| 对话结算 typed 单队列（Wave 1–3） | ✅ | `DialogueSettlementQueue` 的 11 个 `DialogueJobKind` 共用一个无界、非 durable 的 `asyncio.Queue[DialogueJob]` 和一个 consumer；admission 在同一无 `await` 临界段完成 sequence、payload 深拷贝、锚 transition 分类/预约、snapshot 与 `put_nowait`。队列显式跟踪 active job，并以 `ready_for_interactive_submission` 区分“可以立即执行的短用户命令”和“正被长 LLM job 占用”；pending-open 只在前者为真时 admission，否则返回结构化 busy 让客户端重试，不把 required 状态变更排到长任务后留下半截 durable 状态。`AnchorAdmissionRegistry` 显式区分 `persisted/reserved/failed/absent/not_applicable`，同 ref builder 各自 owner-only resolve，failed head 前移且旧引用排空即 GC；每个 dispatch 完成后会从显式 target、冻结 snapshot 或 builder transition 推导受影响 ref 并刷新 durable actual state，因此 targetless `learn` 的内嵌直调与 builder follow-up 解锚都不会遗留旧 generation；刷新仍受 sequence fence 约束，不能把全局 latest 从更晚的同 ref / 跨 ref reservation 拉回。worker 只消费 frozen snapshot：target-specific absent tombstone 不会升级成执行时出现的新锚；锚 relation 与普通 chat settles 在 actual worker 的 coroutine 调用链内直接 `_apply_*`，不递归排队。actual worker、任意层 active child 与 job 结束后的 detached stale child 调用 `submit()` / `submit_and_wait()` 都立即抛 reentry，不 inline dispatch。API runtime 的卡片四动作、pending-open、reconcile、legacy、普通对话学习/结算、锚建立/释放/恢复、探针对话、疑惑 reply/open/attribution replay 均只由队外 producer submit 这一个队列。进程重启可丢失尚未执行的内存 job；durable turn/receipt 保留事实，客户端重试 action 或 GET reconcile 可重新提交，不引入 job table。 |
| 对话结算 worker permit 护栏（`soul/dialogue_settlement_guard.py`，Wave 1–3） | ✅ | actual worker 在 `_run()` 登记 `asyncio.Task` + lifecycle nonce，并只在该 task 的 dispatch context 激活；所有生产 protected mutator façade 均已安装 `require_dialogue_settlement_worker()`。guard 不提供 inline/delegated child 授权：worker 创建的 child 即使继承 `ContextVar` 也不能 mutation，父 job 结束与下一 job 开始都不会改变拒绝结果。热重载严格按 accepting drain old → atomic pause → exact revoke old → start/register new → publish new 交接：drain 等待期间仍接收外部 job，`join()` 返回到 `_accepting=False` 之间没有 `await`，不会出现观察到 idle 后又漏进一个 job 的缝隙；超时则从未进入 paused 状态。失败回滚只给已 drain 的 old worker 分配 fresh nonce，旧 `finally` 只能 `clear_if_current` 自己的旧 tuple。API request task、后台 child 与第三个 direct compatibility callsite 均 fail closed；CLI/OpenClaw 两处显式 `legacy_direct` 位于 runtime guard 边界之外。 |
| 对话学习 LLM 在线内串行（Wave 1–3） | ✅ | runtime dispatcher 的 `learn` 分支在 `_background_admission_bypass` 内由唯一 worker 直接 await 完整 `learn_from_dialogue`，不 detached、不进 task registry，也不在整段 mutation 外包 timeout；provider 自身有限 timeout 保持不变。双 job 阻塞验收固定 `max_active=1`，0.5 秒窗口 heartbeat ≥10，force_tick/exploration/OpenClaw 调用均为 0；queue 结构化记录每项 `queue_wait_ms` / `run_ms`。探针 classifier 也只在对应 typed job 中调用一次，弱正向的 `ExplorationIntent` 在 worker permit 结束后交回既有 exploration 路径，未把 exploration writer 吞进队列。 |
| 对话确认入口与锚（Wave A–D + 单队列 cutover，v0.3.182+） | ✅ | `DialogueAnchorManager` 持久化至多一个 `{kind,ref,generation,established_at,unrelated_streak,origin_turn_id,ambiguous_count}`，四种释放为结算、连续两轮 unrelated、2h TTL、replaced。card discuss 在 admission 先建立 owner reservation，worker 内把 durable payload 从 `pending` 改为 `discussing` 后建锚；建锚失败立即补偿回 `pending`。不存在 `attempt_token/discussing_at` CAS 或 stale scanner；GET 只提交 `card.reconcile`，由 worker 把没有对应 active anchor 的 orphan `discussing` 校正回 `pending`。学习任务入 LLM 前校验一次 ref+generation；LLM 返回后的首个持久副作用由 `note_relation(expected_generation=...)` 在同一状态锁内完成重读+CAS，engine 必须消费返回值，失配整批丢弃、WARNING 并写 `anchor_stale_generation_drop`。结算赢家 payload 固化 `anchor_generation`，applied 收据只能释放该代，同 ref 新锚不会被旧收据碰掉。待聊列表主动 open 以 `pending_open` 建锚且不受 12h/72h 时间 gate；这种卡片仍保持 `pending`，其 defer 会先持久化 `deferred`，再按 origin turn + generation 精确释放锚，不能只覆盖传统 `discussing` 卡。系统疑惑提问也建锚，系统假设卡等待用户操作。Dialogue 回灌统一读取所有 session 的 completed `{chat,hypothesis,confusion}` scope（含 agent-only 疑惑 question，probe 仍排除），而 API turn 列表继续按 session 过滤；durable 请求把产生端 session 逐请求传给学习 payload。新客户端只在对话卡片主动结算假设，三处认知更新区与 CLI 列表均只读；deprecated legacy API 仅为旧客户端转发兼容。归属矩阵、ambiguous/Jaccard 防双计与 confusion FIFO 语义不变。 |
| Turn 级上下文绑定（2026-08-01） | ✅ | `DialogueTurnContext` / `DialogueTurnBinding` 是 frozen typed value object；digest 覆盖 canonical target 事实而不覆盖 capture 时间。API 在 user INSERT 前冻结 `kind/ref/generation/title/evidence`，`SocraticDialogue`、learn queue、engine analyzer、raw event、candidate/ledger provenance 与 settlement 只消费同一 binding；bound stale 只能 drop，不能读取 current anchor 猜测归属。CLI/OpenClaw 继续显式 `legacy_direct` 兼容。 |
| 三端「聊聊口味」长列表与证据展示（v0.3.191+） | ✅ | `/web`、`/m/` 与扩展 side panel 的确认卡 / 疑惑提问都按自然高度进入独立滚动区，不会在固定页高中共同缩短后被裁掉；待聊 inbox 自己限高滚动，底部 composer 始终留在视口。三端重绘遵循 stick-to-bottom：只有读者原本贴近底部或主动发送 / 进入页面时才跟随最新消息，向上阅读则保留 `scrollTop`，并按 durable turn id 恢复已展开依据；移动端额外保留草稿与焦点。共享 renderer 会过滤纯数字、UUID、事件 / note 前缀、BVID 与裸哈希等机器 ID，若无可读证据则整块「依据」隐藏；这只是 UI 展示清洗，不修改 durable payload 或内部证据归属。移动 Web、桌面 Web 与扩展 side panel 现按 `session=popup` 读取并对齐 `{chat,hypothesis,confusion,probe,avoidance_probe}` 可见历史；`delight` 仍留在推荐卡的独立内聊中。真实三端请求验证覆盖长卡、待聊独立滚动、探针聊天跨消息 / 主对话恢复、结算状态跨客户端投影与 composer 可见性。 |
| 对话窗口 + 时间事实（v0.3.182+） | ✅ | `DIALOGUE_WINDOW_TURNS=20`：`_history_to_messages` 截断到最近 20 轮。每个历史 turn 用创建时定死的本地绝对前缀 `[MM-DD HH:mm]`，SQLite 无时区 `created_at` 由公开 `format_dialogue_turn_timestamp(..., local_timezone=...)` 单点转本地；当前时间只追加在当轮 user prompt 尾部，不改写历史前缀。带数据库的非 CLI Dialogue 回灌所有 session 的 completed `chat/hypothesis/confusion`（probe 排除），API 可见列表仍按 session 过滤。 |
| 对话结算 settles（v0.3.182+；单队列 executor） | ✅ | `build_dialogue_insight_prompt(..., anchor=None)` 保持模块级静态 system + `sort_keys=True`，无锚输入/输出逐字节不变；非空 anchor 只在 user message 加契约。`learn_from_dialogue` 仅在**无活锚的 scope='chat'** 处理检索式 settles；锚定轮跳过检索式 settles，`support/contradict/revise/answer` 由锚处理器在当前 worker 内调用 `_apply_*`。普通 `speculation/insight/confusion` settles 同样直接调用 worker-only apply，不再 submit 自己，也不直调旧 direct executor。apply 总是先采用 stored winner payload，按 frozen kind/ref/generation 做 exact validation；stale/failed dependency 在 receipt 前终止。故障边界固定为 event → object → derived → rebuild marker → `applied=1` → projection → anchor release，并提供七个精确 checkpoint。object、derived upsert、marker set-union 与 ledger stable key 均可安全重放；`applied=1` 后只走 publication-only，不再调用前三类 mutator。白名单仍等于当轮 `active_list`，台账保留 `turn_id`；hash8=SHA-256(NFC+strip+空白折叠)hex 前 8，碰撞升 hex16。 |
| 疑惑对象「看不懂」（`soul/confusion.py` + `confusions` 表，v0.3.175+） | ✅ | 当系统无法干净解读某行为时产出**疑惑**（不写画像，只驱动澄清与冻结）。两产生源：①觉察——`analyze_with_confusions()` + 独立 builder `build_awareness_with_confusions_prompt`（静态 system，入 invariance 清单；`analyze()`/`build_awareness_prompt` 一字不动，`cognition_cycle` 切新 API 属有意变更），候选 ≤2/轮、白名单校验落库；②推测僵局——`SpeculatorTickResult.stalemate`=expire 时 `0<confirmation_count<threshold`（现存字段判定），pipeline 转疑惑。状态机 `open→clarifying→resolved\|dismissed`（+TTL `expired`）；`clarifying` 全局 ≤1 由 partial unique index 跨连接原子保证。TTL 扫描并入 12h `cognition_cycle` |
| 疑惑澄清三路 + 唯一结算所有者 + 冻结（v0.3.182+） | ✅ | **ask**：durable chat `scope="confusion"` 先 claim `clarifying` 并立即建锚，72h 冷却持久化于 `asked_at`；待聊列表的显式用户 open 可传 `ignore_cooldown`，只绕过时间冷却，数据库 partial unique index 的全局 `clarifying <= 1` 仍强制生效。API 完成侧效应不再调用 `resolve/defer`，唯一所有者是串行学习队列中的锚处理器。分类结果先写 `confusions.replay_queue`（FIFO、上限 5、超限逐出最旧并记 dropped 台账），队头失败保留，新轮只能入尾；成功后从队头续跑。四种锚释放都会清队列并记 dropped；12h `CognitionCycle` 先重放**任意状态的非空队列**（覆盖 resolve 已提交、pop 前崩溃的 terminal 行，并续做未 applied 对象收据），再扫描晚于 ask receipt 且 completed、尚无 payload receipt 的 classification gap。三出口仍为 `real_interest` / `proxy_behavior` / `dismissed`；两次 ambiguous 走 defer，恢复 open 行时不重复增加 defer_count。topic 冻结与 held-update 重放状态机保持不变。 |
| 态势门控（`soul/posture_gate.py`，v0.3.176+） | ✅ | 深层写入一致性门控（Phase 3）。`build_posture_gate_prompt` 静态 system（三判定 accept/downgrade/reject + 「冲突不是错误是新假设」）+ `sort_keys`（入 invariance 清单）。`PostureGate` 三模式：`off`=完全旁路、**门控 LLM 零调用**、逐字节等价；`shadow`(默认)=**commit boundary 捕获不可变快照**（before/after/source_refs/gate_id），异步旁路任务只消费快照（判定前对活状态再写入不污染判定，带断言）、判定落台账 `shadow_*`、provider 异常/非法 JSON/非白名单 verdict 均落 `shadow_error`、**零延迟不阻塞原写入**；`enforce`=同步判定，同一组判定错误保守 downgrade 且 `GateDecision.is_error=True`，供重建调用方保留 marker 重试；只有明确白名单 accept/downgrade/reject（含真实 refusal）为 `is_error=False`。**深层线归一后**（见「深层影响唯一模式」），有效接入点为两条：①对话 goal/value/state 深层候选（interest/dislike 快线不过；downgrade 置信=confidence×0.6 转 insight）；③soul 整份重建，泛化承载三触发源（dialogue / feedback_batch / confirmed_hypotheses），downgrade/reject→放弃本次 rebuild + 台账。**接入点②（管线 VALUES/CORE 层 updater 门控）已随 P1 退役**：`update_layer` 对 VALUES/CORE 直接封死 no-op + WARNING，不再有逐层门控。新 caller `soul.posture_gate` 注册 usage recorder。enforce 受 save-time 三条件校验（见 config.md，`posture_gate_force_enforce` 逃生门） |
| 疑惑代理行为证据折价（v0.3.176+） | ✅ | 疑惑 `resolve()` 走 `proxy_behavior`（误读）出口时，对 `evidence_refs` 中可解析为事件 id 的关联事件调用 `Database.discount_events_by_confusion`（`sources/event_format.apply_confusion_discount`）：盖 `metadata.discounted_by_confusion=true` + `signal_strength` 折至 0.2（与 retraction 折价同底、幂等不回升）。非 id ref（话题/note）跳过；`real_interest` 出口不折价 |
| topic 生命周期状态机（`soul/topic_lifecycle.py`，v0.3.177+） | ✅ | interest（flat 与 Onion domain 两层）叠加状态元数据 `state ∈ {trial\|active\|decaying\|archived}` + `evidence_count` + `last_evidence_at` + `parent_topic`；旧数据缺字段默认 `active`，且**默认 `active` 的 topic 序列化不写这些键**（回放门：`interest_tag_to_dict` 只在非默认时 emit）。跃迁（常量带首轮校准注释，见模块 docstring）：新 topic 首见→`trial`；证据 ≥5 **或** 持续 ≥7 天→`active`（`apply_evidence`，接在 `analyze_events`/`learn_from_dialogue`/反馈批**以及增量 pipeline `layer_updaters._update_interest`** 的偏好写 chokepoint，每次分析计一次证据；增量 pipeline 是日常浏览的主路径，早期漏接导致这条路径产出的 interest 没有 `state`，而 `get_state()` 把无 state 读成 `active`——新 topic 因此跳过 trial 直接参与推荐权重，12h `scan_lifecycle` 也不会补，因为无 state 的 interest 看起来已经是 active）；`last_evidence_at` 静默 ≥30 天→`decaying`（权重×0.5）；再 ≥30 天（共 60 天）→`archived`（不删）；`archived`/`decaying` 遇新证据→直接复燃 `active`；**衰减扫描 `scan_lifecycle` 并入 12h `ProfileConsolidator`**（`last_evidence_at` 缺失的旧 topic 永不被扫衰减，避免启用即团灭）；**dislike 改「归档+避雷」**（`archive_topics`：匹配 topic 置 `archived` 保留台账，不再从库删）；**细分提议**（子类占父域权重 ≥60%）只经 `topic_subdivision_proposal` 记台账不执行（shadow）。所有跃迁进 `profile_update_ledger`（`topic_lifecycle` 写点）。**最小消费**：见下「觉察提炼节奏」下方 `topic_lifecycle_serialization` 开关（默认 off） |
| 觉察提炼节奏（`soul/cognition_cycle.py`，v0.3.182+） | ✅ | 除 12h 兜底节流外支持未提炼事件 ≥30 或强信号提前触发；`asyncio.Lock` 保证 due-check + watermark 单飞，state JSON 以 tmp+fsync+`os.replace` 原子写，失败/取消不推进水位。每次真正 due 的周期还调用 `confusion_replay_hook`：先重试已持久化 classifier 输出，再扫描 completed clarification crash gap；失败 best-effort 留待下轮，不阻断 awareness/insight 与 pending rebuild hook |
| 兴趣探针聊天情绪判断 | ✅ | `/api/interest-probes/respond` 的 chat 分支会先让对话引擎回复，再用非 JSON 的单词分类 LLM 调用判断 `strong_positive / weak_positive / neutral_deferred / neutral / negative`（系统提示是 `llm/prompts.py:build_probe_sentiment_prompt` 的静态常量，走 prompt 缓存），失败时回退关键词；强正向直接确认，弱正向进入短期探索 buffer，`neutral_deferred`（用户主动说「先放着」「稍后再看」）走 defer 搁置状态机，`neutral`（态度模糊，如「再看看」）不改状态，避免一句“有点意思”立刻写成长期兴趣 |
| 账户同步事件分析 | ✅ | 后台低频同步导入的 `view/favorite/follow` 事件会复用 `analyze_events()` 进入偏好与画像链 |
| 小红书初始化画像信号 | ✅ | `openbiliclaw init` 会把插件解析到的小红书 `saved/liked/xhs_history` 转成 `favorite/like/view` 事件，并与 B 站历史、收藏、关注一起进入 `analyze_events()` 和初始画像 history |
| 抖音初始化画像信号 | ✅ | `openbiliclaw init --yes-douyin` 会把插件解析到的抖音 `dy_post/dy_collect/dy_like/dy_follow` 转成 `view/favorite/like/follow` 事件，并进入偏好分析和初始画像 history |
| GitHub 初始化画像信号 | 🧪 接线；验收见 ledger | 选中 GitHub 且解析出公开账号后，后端通过官方 REST 读取 public starred repositories，并把每个 Star 转成 `favorite` 事件进入同一偏好分析与初始画像 history。它不依赖扩展、不读取私有仓库，也不进入后台增量账号同步；真实终态见 GitHub acceptance ledger |
| Durable 行为事件增量画像 | ✅ | profile 已存在时，`POST /api/events`、推荐点击与带画像语义的 source task 只经 `EventIngressService` 提交 durable event 并 wake。app-owned scheduler 的 `profile_events` generic consumer 与 `content_feedback` consumer 按显式 owner、各自 cursor 扫描，使用 event-row 稳定 signal ID，通过 `checkpointed_enqueue_batch()` 原子发布 buffer+cursor，再调用 `tick_if_buffered()`；独立周期维护才调用完整 `tick()`，HTTP 不直调 pipeline/LLM。retraction 投影在 generic cursor 前完成；hypothesis/import feedback 由其它 owner 处理或只越过 feedback cursor；rejected/not_initialized 不入 pipeline。 |
| 小红书 / 抖音 / YouTube / 知乎 / Reddit / Linux.do 增量画像事件 | ✅ | profile 已存在时，带画像更新语义的 bootstrap task-result 新增事件会经 durable ingress 后进入 generic profile-update owner，参与后续分层画像更新；知乎 / Reddit / Linux.do 普通 fetch smoke 默认不进入画像，周期任务则由后端 `incremental=true` 标记放行；Linux.do 只接收插件归一化后的事件，不接收 Cookie 或原始响应 |
| Retraction 确定性折价（双面） | ✅ | 用户撤销的正向行为（unlike/unbookmark/unfollow/undo-retweet）不再以满强度留在画像证据里。**内存面**：`ProfileUpdatePipeline.ingest_batch()` 开头新增原子折价预处理，早于任何阈值消费（`_update_layer`）——同批 / 既有缓冲中同 identity key、事件类型 == `retracted_action`、且事件时间早于 retraction 时间的正向信号被折价（`metadata.retracted=true`、`signal_strength=min(现值,0.2)`）；乱序到达用内存 tombstone `(identity_key, action) → retraction 事件时间`（TTL 24h / cap 500 逐出最旧）处理，`like→retract→like` 的重新点赞（事件时间晚于 retraction）不折，事件时间缺失保守不折。**离线重读面**：`Database.mark_positive_events_retracted()` 由 generic durable event consumer 在推进 cursor 前严格投影，并被 `openbiliclaw init` 全量重建 / 12h 认知整理等重读路径复用；旧 `apply_retraction_db_marks()` 只保留 deprecated embedder 兼容，不再由 HTTP ingress 直调。迟到正向事件（account_sync 回填旧 like）在 MemoryManager 规范化落库时对账已存 retraction 行。identity key 复用共享 `sources/identity_keys.py`（tweet_id / bvid / mid / xhs note_id）。`retracted_action` 白名单 `{like,favorite,share,follow}`，越界跳过 + WARNING。|
| Retraction 渲染标记与回放不变性 | ✅ | `sources/event_format.render_retraction_marked_events()` 给 `metadata.retracted` 为真的事件在渲染时给 context 追加「(已撤销)」——两个重读 events 的 LLM 消费面（`build_preference_analysis_prompt` 偏好 + `build_awareness_prompt` 12h 认知觉察）共用该函数自动生效，兼容 dict 与 DB 返回的 JSON 字符串两种 metadata 形态；折后 0.2 强度经既有 preserved keys 自然进入 prompt。偏好 system prompt 追加一条静态撤销语义规则（rule 12b，仍是模块级常量，prompt-cache 调用不变性不破）。回放不变性作用域=事件渲染文本：无 retraction 标注的事件集渲染字节一致（`tests/test_event_retraction_discount.py::test_event_rendering_invariance_without_retractions` 兜底）|
| ToneProfile | ✅ | 从 `OnionProfile`、偏好摘要和近期反馈推断 `density/warmth/playfulness/directness`，统一驱动推荐、画像和聊天语气 |
| Cognition updates | ✅ | 在反馈刷新和聊天学习后生成 `interest_added / dislike_added / profile_shift` 结构化 cognition card，包含 `summary / context_line / source_label / expand_hint / impact / reasoning / evidence / source / created_at`，供插件提醒与画像页展开展示；即时反馈和聊天会尽量指出具体内容或本轮聊天，聚合判断则保守回退到”基于最近几条相关内容” |
| Layered profile cognition | ✅ | `OnionProfile` 新增 MBTI / Values / Interest 等分层，画像生成会同时消费 `history + preference + awareness + insights`，避免把兴趣 topic 堆成整段画像 |
| 猜测兴趣系统 | ✅ | `InterestSpeculator` 定期通过 LLM 过采样生成猜测兴趣方向，并按 `[scheduler]` 的 generation interval、TTL、cooldown、确认阈值和上限运行；候选带 `probe_mode=near/lateral/bridge/wildcard` 四档距离，普通 `near` 池最多 5 条，`lateral/bridge/wildcard` 挑战池单独最多 3 条；通过事件或用户确认后按来源权重转正为正式兴趣，未确认则拒绝并冷却；`tick/force_tick` 会读取最新 `probe_feedback_history`，确认/拒绝/探针聊天后的 domain 不会被旧快照重新生成 |
| 探针「暂时忽略」（defer/搁置） | ✅ | 用户对探针点「暂时忽略」（或聊天说「先放着」）时，`user_defer_speculation` 把探针置为 `status="deferred"` 并按阶梯隐藏：第 1 次 7 天、第 2 次 14 天（`PROBE_DEFER_DAYS`），第 3 次（`PROBE_MAX_DEFERS`）耗尽转 `rejected` + 30 天 cooldown（走 TTL 过期语义，记 `defer_exhausted`，**不**进 handled 集，冷却后可重新猜——区别于显式 reject 的永久拉黑）。`deferred` 探针从所有读侧（pending 端点 / WS 推送 / `get_active_speculations`）消失；`tick/force_tick` 维护段的最后一步 `revive_deferred` 在 `deferred_until` 到期后把它复活为 `active`（重置 `created_at` 给新 TTL 窗口、`confirmation_count` 夹到阈值-1，保证复活后先以探针形式再露面而非静默转正）。避雷探针有对称的 `user_defer_avoidance` / `revive_deferred_avoidances`（复活排在 compaction 之后） |
| 桌面 Web 探针即时反馈与撤销 | ✅ | 正向兴趣和避雷探针的 `confirm / reject / defer` 在消息抽屉与画像页复用同一稳定 action key，先即时隐藏或更新卡片，再保留 10 秒撤销窗口；撤销不调用 respond API，提交失败恢复原卡。`chat` 需要对话回复和情绪分类，继续直接调用后端，不进入可撤销屏障。 |
| 短期探索 buffer | ✅ | `exploration_buffer.py` 把弱正向聊天、推荐喜欢、惊喜喜欢、普通点击和负反馈汇总到 `discovery_runtime_state["short_term_exploration_buffer"]`；7 天内显式弱证据累计到阈值后以 `buffer_promoted` 写回兴趣，负向反馈会进入 48h 冷却并抵消分数 |
| 不喜欢领域探针系统 | ✅ | `AvoidanceSpeculator` 与正向兴趣探针并行运行，最多 5 条 active 避雷假设；只在用户确认或显式负向证据达到阈值后写入 `disliked_topics`，未确认前不参与 discovery / recommendation 过滤；生成前会读取最新 `avoidance_probe_feedback_history`，确认/否认/探针聊天处理过的方向不再作为 active 避雷探针重复出现 |
| INTEREST 更新带认知语境（2026-07-27+） | ✅ | `_update_interest` 把近期认知尾巴（`profile.recent_awareness[-5:]` / `active_insights[-5:]`，与 `regenerate_portrait` 同窗口）经 `analyze_events(awareness_notes=…, active_insights=…)` 传入偏好分析 prompt 的 `<recent_awareness>` / `<active_insights>` 段——小批事件不再在真空里被解读。init 分片与反馈批刻意不传（init 无认知、反馈按字面判断），不传时 prompt 逐字节等于旧版（`TestPreferencePromptCognitionContext` 钉死）。真实 LLM A/B：top-8 兴趣保持不变，带语境版把同主题事件归并进既有兴趣而非另立条目 |
| ROLE 增量更新器 | ✅ | `_update_role`（`build_role_delta_prompt`，基于信号证据 + LLM diff-protection）；ROLE 是最深的快线层，仍由 pipeline 增量更新 |
| ~~VALUES/CORE 增量更新器~~（P1 已退役） | ⛔ | `_update_values` / `_update_core` 仍作为 delta-prompt 库函数保留（直接工具/潜在重建输入复用），但**已从 pipeline dispatch 摘除**：`update_layer` 对 VALUES/CORE 封死 no-op + WARNING。深层变更改由「假设确认 → 门控下 soul 重建」唯一模式驱动，见下文「深层影响唯一模式」 |
| v0.3.74 Soul 结构化 JSON 容错统一 | ✅ | ProfileBuilder、PreferenceAnalyzer、DialogueInsightAnalyzer、AwarenessAnalyzer、InsightAnalyzer、LayerUpdaters 和 InterestSpeculator 都收敛到 `llm.json_utils`，每个任务用 predicate 约束自己需要的 schema；MiMo / 非 OpenAI wrapper 不再只修 awareness 一处 |
| v0.3.147 画像上下文缓存前缀保护 | ✅ | PreferenceAnalyzer、ProfileBuilder、AwarenessAnalyzer、InsightAnalyzer、InterestSpeculator 和 AvoidanceSpeculator 的结构化 prompt 已经把 history / preference / soul_profile / profile_summary 放在 user message；调用 `LLMService` 时在支持路径上关闭额外 core memory 注入，避免把同一份动态画像再次拼进 system prompt |
| 画像 → LLM 序列化门面 (`soul/profile_views.py`) | ✅ | 三个内容管线序列化器 `build_profile_summary` / `compact_content_prompt_profile_summary` / `build_query_generation_profile_summary` 及 archived-topic 序列化开关的规范实现都在本模块；`discovery/strategies/_utils.py` 只保留向后兼容 re-export。所有 view 是**有效画像的纯确定性函数**（同输入两次调用字节一致），三个内容管线 view 均排除 `personality_portrait`。字节对拍见 `tests/test_profile_views.py`（3 画像 × 3 view = 9 组），结构守护断言三序列化器仅定义于本模块 |

## 画像 → LLM 序列化门面 (`soul/profile_views.py`)

所有把画像对象转成 prompt 文本 / dict 的序列化逻辑集中在 `soul/profile_views.py`
一个模块（spec 不变量 V1）。`discovery` / `recommendation` / `runtime` / `sources`
的内容管线消费这些 view，不再各自造序列化分支；历史 `discovery/strategies/_utils.py`
导入路径通过 re-export 保持不断。

`set_topic_lifecycle_serialization()` / `topic_lifecycle_serialization_enabled()` 同样由该门面
持有进程级开关；`build_profile_summary(exclude_archived_topics=None)` 默认读取它，并在开启时
同时排除 Onion domain 与 flat tag 的 `archived` 项。API、CLI 与 replay 都应直接设置这一
canonical owner；`discovery.strategies._utils` 的同名符号仅用于旧调用方兼容。

三个 public view：

- `build_profile_summary` — 规范结构化画像（排除 `personality_portrait`），所有源平台
  内容 prompt 的统一输入。
- `compact_content_prompt_profile_summary` — 对 `build_profile_summary` dict 做高频
  内容 prompt 的裁剪（20 核心 / 48 兴趣 / 32 域 × 16 specifics / 12 recent，长期避雷
  不裁剪）。
- `build_query_generation_profile_summary` — discovery 关键词 / 领域生成用的查询瘦身
  口味 view（MMR 多样化、embedding 可选）。

另有两个字符串 view：`chat_core_memory`（聊天核心记忆拆 stable/volatile 两块，Task 6）
与 `speculation`（猜测器 prompt 的画像段，Task 7）。`speculation` 委托画像自身的
`to_llm_context(include_portrait=False)`，把兴趣猜测器 / 避雷猜测器此前各自内联的字符串
分支收口进门面（零行为变化、排除画像），由 `tests/test_profile_views_guards.py` 的 sentinel
排除 + 两次调用字节一致守护，并对拍 `tests/golden/profile_views/speculation__*.txt`。

两个仅供 discovery 侧调用的叶子工具（`normalize_match_text` /
`_coerce_query_embedding_vector`）也落在本模块并由 `_utils` re-export：查询生成 view
依赖它们，而 `soul` 层**不得** import `discovery`，因此它们下沉到 soul 后再回流。

> 新增携带画像的 prompt 前先查 [画像使用登记表](../profile-usage.md)，并复用本模块
> 的 view，不要自建序列化器。

## 猜测兴趣系统 (Speculative Interest Lifecycle)

系统会主动探索用户可能感兴趣但尚未接触的领域。通过心理学桥接推理，从已有兴趣模式中推断新方向。

### 生命周期

```
生成 (Generate) — LLM 根据画像猜测 3-5 个新方向（每 10min / init / 启动时）
    ↓  受活跃猜测数上限限制，到达上限则跳过
活跃 (Active) — 每次事件 ingest 做关键词匹配观测
    ├→ confirmation_count >= threshold → 转正 (Promote)
    │    创建 InterestDomain(source="speculated", weight=0.3)
    │    合并入 OnionProfile.interest.likes
    └→ TTL 到期未确认 → 拒绝 (Reject)
         加入冷却列表 (cooldown_days=7)
         冷却期间不再猜测该方向
```

### 数据结构

- **SpeculativeInterest**: domain, category, reason(心理学桥接), experience_mode, entry_load, `probe_mode`, confidence, ttl_days, confirmation_count/threshold, `confirmation_source`, `confirmed_at`, status(`active/confirmed/promoted/rejected/deferred`), `deferred_at`, `deferred_until`, `defer_count`
- **CooldownEntry**: 被拒绝的方向 + 冷却到期时间
- **SpeculativeState**: 活跃猜测 + 冷却列表，存储在 `data/memory/speculative_state.json`

### 两个猜测来源

1. **周期性生成**（默认每 10min）：专用 prompt `build_speculation_generation_prompt()` 深度推理，并额外标注 `experience_mode` / `entry_load`。Init 和进程启动时强制触发一次
2. **偏好分析附带**：`PreferenceAnalyzer` 每次分析事件时产出 `speculative_interests`，作为种子注入

### Active Pool 多样性

- generation 不再把 LLM 返回的前几条候选直接塞进 active pool，而是先过一层本地 balanced selector
- selector 会把既有 active pool 也作为选择上下文，优先补缺失的 `experience_mode` / `entry_load`，再按 confidence / weight 补齐剩余槽位
- selector 还会执行 distance-band quota：当存在挑战候选时 `near` 最多约占 40%，并尽量保证 `lateral/bridge/wildcard` 至少有一条进入 active pool
- 当模型没有提供足够丰富的候选时，会自动降级回普通排序，不阻塞 speculative 生成

### Probe Distance Bands

`probe_mode` 是探针距离，不直接作为用户文案：

| probe_mode | 语义 |
|------------|------|
| `near` | 靠近已知兴趣的低风险确认 |
| `lateral` | 同一能力 / 审美 / 需求下的横向相邻方向 |
| `bridge` | 从已知兴趣桥接到另一个内容域，挑战但可解释 |
| `wildcard` | 更远的探索项，用于打破短期口味收窄 |

`SpeculativeInterest.challenge` 对 `lateral/bridge/wildcard` 返回 `True`。`GET /api/profile-summary`、`GET /api/interest-probes/pending` 和 `interest.probe` runtime event 都会暴露 `probe_mode` 与 `challenge`，让 UI 可以区分普通确认和挑战探针。挑战探针有独立 active 额度：普通 `near` 继续使用 `scheduler.speculation_max_active`（默认 5），挑战池固定最多 3 条，不再被 5 个普通探针占满后挤掉。

### Probe Novelty Guard

- LLM 生成候选和 `PreferenceAnalyzer` seed 注入都会经过 `ProbeNoveltyGuard`
- guard 会收集画像 `interest.likes[*].domain`、画像 `specifics[*].name`、active speculation、cooldown speculation、近期 probe history 和已处理 probe feedback
- 第一版使用规范化字符串和中文 bigram overlap 做本地判重，不引入 embedding 成本
- 与已有画像 domain / specific、active / cooldown、近期 `probed_domains`、`probe_feedback_history` 中 `confirm/reject/chat_positive/chat_negative/chat_rejected` 等已处理记录明显重复的候选会被丢弃；候选 specifics 若部分重复，会先移除重复细项，剩余不足 2 条时丢弃候选

### 配置项

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `scheduler.speculation_interval_minutes` | 10 | 生成间隔（分钟） |
| `scheduler.speculation_ttl_days` | 3 | 猜测存活期。注意：`SpeculativeInterest` 数据类本身的 `ttl_days` 字段默认值为 14，仅作为反序列化不含该字段的历史数据时的兜底值；实际新产生的猜测兴趣均使用此配置项的 3 天 |
| `scheduler.speculation_cooldown_days` | 7 | 拒绝后冷却期 |
| `scheduler.speculation_confirmation_threshold` | 3 | 转正所需确认数 |
| `scheduler.speculation_max_active` | 5 | 最大活跃普通 `near` 猜测数；挑战探针 `lateral/bridge/wildcard` 另有固定 3 条 active 额度 |
| `scheduler.speculation_max_primary_interests` | 15 | 活跃猜测一级上限；不再把已确认兴趣计入，避免画像丰富后探针系统永久停摆 |
| `scheduler.speculation_max_secondary_interests` | 60 | 活跃猜测二级上限；不再把已确认细项计入，避免画像丰富后探针系统永久停摆 |
| `scheduler.speculator_idle_interval_minutes` | 30 | `ProfileUpdatePipeline` 空闲时检查猜测兴趣生命周期的间隔；`speculation_interval_minutes` 仍作为 speculator 内部生成间隔 gate |

### 触发时机

| 场景 | 方法 | 说明 |
|------|------|------|
| 定时 | `tick()` via Pipeline | 空闲 pipeline 默认每 30min 检查一次猜测兴趣生命周期；真正生成新猜测还受 `speculation_interval_minutes` 默认 10min gate 和兴趣上限约束 |
| Init | `force_tick()` via `build_initial_profile()` | 画像初始化后立即生成猜测 |
| 进程启动 | `force_tick()` via `startup_refresh_loop()` | API 启动时确保有活跃猜测 |
| 偏好分析 | `ingest_seeds()` via `_update_interest()` | PreferenceAnalyzer 附带的推测兴趣注入 |

`force_tick()` 忽略间隔计时器，但仍尊重普通 `near` 上限和独立挑战上限；即使 5 条普通探针已满，只要挑战池未满，仍会尝试生成挑战探针。生成 prompt 会根据空位写入动态 `probe_mode_request`：near 池满时明确要求只输出 `lateral/bridge/wildcard`，挑战池满时只输出 `near`，最终入池仍由本地 slot selector 硬约束。

### 兴趣上限机制

当活跃猜测达到上限时，跳过生成。已确认兴趣不再计入生成上限，否则画像越丰富越容易让探针系统永久停摆：

| 级别 | 计算方式 | 上限 |
|------|---------|------|
| 一级 | 活跃猜测数 | 15 |
| 二级 | 活跃猜测数 | 60 |

### Pipeline 集成

- `ingest_batch()` 时调用 `speculator.observe()` 做轻量级关键词匹配
- `tick()` 时调用 `speculator.tick()` 处理过期/转正/生成
- 转正后自动创建 `InterestDomain` 并记录 changelog

### Discovery 集成

- `SoulEngine.get_profile()` 自动将活跃猜测附加到 `profile._active_speculations`
- `build_profile_summary()` 读取 `_active_speculations` 并包含在画像摘要中
- `SearchStrategy` / `ExploreStrategy` / `TrendingStrategy` 均可在 LLM prompt 中看到猜测兴趣

### API 集成

- `GET /api/profile-summary` 返回 `speculative_interests` 字段（`SpeculativeInterestOut` 列表），包含 `probe_mode` 与 `challenge`
- 从 `speculative_state.json` 直接加载，最多返回 6 条活跃猜测
- `POST /api/interest-probes/respond` 的 profile 页面确认会传 `surface="profile"` 并记录为 `profile_confirmed`；runtime/inbox 卡片确认默认仍是 `probe_confirmed`；聊天强确认记录为 `chat_confirmed`，buffer 晋升记录为 `buffer_promoted`
- `POST /api/interest-probes/respond` 与 `POST /api/avoidance-probes/respond` 的 `response` 取值：`confirm` / `reject` / `defer` / `chat`。`defer`（暂时忽略）返回 `{ok, action: "deferred"|"defer_exhausted", deferred_until, defer_count}`，并发 `interest.deferred` / `avoidance.deferred` runtime event（耗尽时复用 `*.rejected` event）。`deferred` 不改画像，桌面 Web 因此不对这两个 event 触发 profile 刷新
- 桌面 Web 对 `confirm / reject / defer` 使用 10 秒客户端提交屏障，并以 `probe:<messageType>:<normalizedDomain>` 作为跨消息抽屉/画像页的稳定动作键；同一探针不会在两个 surface 产生两次待提交写入。`chat` 不走该屏障

### Probe 选择

- runtime push 和 OpenClaw `get_next_probe()` 共用同一套 probe selection 规则
- `confirmation_count` 仍然是第一优先级；当验证压力相同，会优先选择最近没推过的 `experience_mode + entry_load` 组合
- probe 去重状态写入并持久化到 `discovery_runtime_state["probed_domains"]`、`discovery_runtime_state["probed_axes"]` 和 `discovery_runtime_state["probed_distance_bands"]`；runtime push 只有在 `interest.probe` 实际投递到至少一个 runtime stream 订阅者后才记录，避免前端离线时误消耗探针
- `/api/interest-probes/respond` 会把真实命中 active 探针的 confirm / reject，以及 chat classification 写入 `discovery_runtime_state["probe_feedback_history"]`；stale / 已处理卡片返回 `ok=false` 时不写历史，避免重复点击污染 novelty 依据。classification 保留 `raw_text_excerpt / classifier / resulting_action` 等审计字段。后续生成会降低 reject / chat_rejected 体验轴的入池优先级，选择会跳过明显重复的 domain，并在同等压力下避开负向反馈过的体验轴与 probe distance
- runtime push 成功投递后、OpenClaw `get_next_probe()` 成功返回后，都会记录本次 domain / axis / probe_mode，连续调用不会重复返回同一条 active probe

### 短期探索 Buffer

弱正向不是长期偏好确认。`short_term_exploration_buffer` 用 10 天 TTL 存储近期探索证据，7 天 promotion window 内满足 `score >= 4.0` 且显式弱正向证据足够时才晋升：

| source_event | 权重 | 来源 |
|--------------|------|------|
| `weak_positive_chat` | `+1.5` | 兴趣探针聊天里的弱正向表达 |
| `card_like` | `+1.5` | 普通推荐卡片喜欢 |
| `card_more_like` | `+1.5` | 惊喜推荐喜欢 |
| `long_watch` | `+0.5` | 预留长观看弱证据 |
| `plain_click` | `+0.25` | 普通推荐点击，只能作为弱辅助 |
| `negative` | `-3.0` | dislike / 不感兴趣，触发 48h 冷却 |

晋升时调用 `merge_confirmed_interest(source="buffer_promoted")`，与手动确认使用同一套兴趣合并逻辑，不重复插入同名 domain。

### 关键文件

- `src/openbiliclaw/soul/speculator.py` — 核心引擎（生成/观测/转正/过期/force_tick）
- `src/openbiliclaw/llm/prompts.py` — `build_speculation_generation_prompt()`
- `tests/test_speculator.py` — speculative lifecycle / novelty / probe selection 单元测试

## 不喜欢领域探针系统 (Avoidance Probe Lifecycle)

系统会主动探索用户可能想避开的内容形态、质量边界或表达方式。它和正向 `InterestSpeculator` 分开存储、分开配额，默认最多 5 条 active，不占正向兴趣探针的 5 条配额。

### 生命周期

```
生成 (Generate) — LLM 根据 dislike、正向边界和风格画像生成 2-4 个细分避雷假设
    ↓  受独立 active 上限限制，到达 5 条则跳过
活跃 (Active) — 只观测显式负向证据
    ├→ 用户 confirm 或 confirmation_count >= threshold
    │    → 标记 confirmed/promoted
    │    → Pipeline/API 调用 apply_new_dislikes()
    │    → 写入 preference.disliked_topics + 同步 soul layer + 清理候选池
    └→ 用户 reject 或 TTL 到期
         → 进入 cooldown，不写画像，不过滤推荐
```

`AvoidanceSpeculator.tick()` 和 `force_tick()` 会在生成、转正、拒绝时输出 INFO 级摘要；active 已满或无 LLM 服务导致 `force_tick()` 无变化时只输出 DEBUG。LLM 返回的候选若被 novelty / quality gate 丢弃，会记录丢弃原因，方便排查“post-reload 已触发但没有新避雷探针”的生产问题。

active 池会做两层多样性保护：词面 / specifics 的 novelty guard 阻止明显重复，source/topic guard 额外阻止同一 `source_mode` 下围绕同一粗主题连续换皮（例如多个 AI positive_boundary 只留一条）。如果历史 active 已经重复，下一轮 tick 会保留更具体 / 置信度更高的一条，其余写入 cooldown；新生成候选也会参考当前 active 的 `source_mode`、`source_signal`、体验轴和 specifics，避免一批避雷探针都围绕同一个证据源。

### 确认语义

- `confirm` 表示“确实不喜欢 / 需要避开”。写回时优先写 `specifics[*].name`；只有 specifics 为空时才兜底写 domain，避免把子方向扩大成整个领域。
- `reject` 表示“我并不排斥这个方向”。它只进入 cooldown 和 `avoidance_probe_feedback_history`，用于后续去重。
- `chat` 使用 `scope="avoidance_probe"` 的 durable chat。用户在多聊中表达“对，这类不喜欢”会走 confirm-like 反馈；表达“不是，我其实可以看”会走 reject-like 反馈；中立只留审计记录。

### 写回路径

确认后的持久化源头是 flat preference：

`apply_new_dislikes()` → `preference_layer.data["disliked_topics"]` → `OnionProfile.populate_from_flat_preference()` → `soul` layer / profile files → pool purge。

`AvoidanceSpeculator` 只维护自己的 `avoidance_state.json`，不直接跨模块修改 `disliked_topics`、`soul` layer 或候选池。API confirm 和 pipeline 自动 promote 都调用 `soul.dislike_writeback.apply_new_dislikes()`，因此手动确认和观察驱动确认走同一条写回与清池路径。

### 观察规则

自动确认只消费高确信负向信号：`feedback_type=dislike`、`reaction=thumbs_down`、`event_type=dislike` 或避雷探针聊天里明确的负向表达。`quick_exit` / `inferred_satisfaction=negative` 这类被动信号不会增加 confirmation count；这是有意严于 preference 层 dislike 抽取的规则，因为避雷探针确认会写入长期过滤偏好。

### 配置项

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `scheduler.avoidance_speculation_interval_minutes` | 10 | 负向探针生成间隔（分钟） |
| `scheduler.avoidance_speculation_ttl_days` | 3 | 负向探针存活期 |
| `scheduler.avoidance_speculation_cooldown_days` | 7 | 否认或过期后的冷却期 |
| `scheduler.avoidance_speculation_confirmation_threshold` | 3 | 自动确认所需显式负向证据数 |
| `scheduler.avoidance_speculation_max_active` | 5 | 最大活跃避雷假设数 |

### 集成边界

- `GET /api/profile-summary` 返回 `speculative_avoidances`，供移动 Web、桌面 Web 和插件画像页展示。
- `GET /api/avoidance-probes/pending` / `POST /api/avoidance-probes/respond` / `POST /api/avoidance-probes/trigger` 提供前端与 OpenClaw 的操作入口。
- `POST /api/avoidance-probes/respond` 只有在 active 避雷探针真实命中时才写入 `avoidance_probe_feedback_history`；已确认、已拒绝或被刷新替换的 stale 卡片会返回 `ok=false`，不会再追加矛盾的 confirm / reject 历史。
- runtime stream 推送 `avoidance.probe`，确认、否认和聊天分别广播 `avoidance.confirmed` / `avoidance.rejected` / `avoidance.chat`。
- 配置热重载后，`RuntimeContext.restart_background_tasks()` 会 detached 调度避雷 speculator 的 `force_tick()`，并传入 `discovery_runtime_state["avoidance_probe_feedback_history"]`；这条 one-shot 与正向兴趣 speculator 共用 `_safe_post_reload_speculate()`，避免阻塞 `/api/config` 响应。
- `ProfileUpdatePipeline.tick()` 调用避雷 speculator 时会捕获并记录 warning，避免 refresh loop 外层的 broad suppress 把异常静默吞掉。
- 未确认避雷探针不会挂到 `profile._active_speculations`，也不会进入 discovery、curator、delight 或 recommendation prompt。

### 关键文件

- `src/openbiliclaw/soul/avoidance_speculator.py` — 负向探针状态机、novelty guard、候选选择
- `src/openbiliclaw/soul/dislike_writeback.py` — confirmed dislike 写回、profile 同步和候选池清理
- `src/openbiliclaw/llm/prompts.py` — `build_avoidance_generation_prompt()`
- `tests/test_avoidance_speculator.py` — avoidance lifecycle / novelty / probe selection 单元测试

## 画像写点台账挂钩清单

> 认知画像流水线 Phase 0。以下每个画像写点在动作结束后经 `ProfileLedger`（`soul/ledger.py`）追加一行台账（best-effort，写失败只 WARNING）。**新增画像写点必须补挂钩并更新本清单（code review 义务）。**

| # | 写点 | write_point | 实现位置 |
|---|------|-------------|----------|
| 1a | 对话学习偏好覆写 | `dialogue_preference_overwrite` | `engine.learn_from_dialogue` |
| 1a′ | 对话深层自述落库 | `dialogue_deep_selfstatement` | `engine._persist_confirmed_deep_candidates`（过门的 goal/value/state 候选落成 `validated=True / user_verdict="confirmed"` 假设——用户第一人称自述即确认；同轮强制门控重建，纯深层自述不动兴趣权重也当轮生效） |
| 1b | 对话学习整份重建 | `dialogue_soul_rebuild` | `engine.learn_from_dialogue` |
| 2 | dislike 清池 | `dislike_purge` | `engine.learn_from_dialogue`（调度时记录） |
| 3 | 管线各层 updater 持久化 | `pipeline_layer_update` | `layer_updaters.update_layer`（SURFACE/INTEREST/ROLE 快线层 changed 时，每层一行；VALUES/CORE 已封死不写）。`source` 通常是 `pipeline:<层名>`；当本批含 FEEDBACK 信号（统一兴趣更新线）时改记 `source="feedback"`，保住反馈线在台账里的连续性——`unified_interest_line=true` 后这一行就是 #4a 退役写点的接班人，反馈线的偏好写入全部在此可查 |
| 4a | 反馈批偏好覆写（**已退役**） | `feedback_preference_overwrite` | 默认统一兴趣线已停写，反馈偏好改由 `pipeline_layer_update(source="feedback")`（#3）承担。历史行只读保留，`openbiliclaw ledger` 仍可查询；仅显式设置 `unified_interest_line=false` 回退旧批线时才会恢复写入 |
| 4b | 反馈批整份重建（P2 已过门控③） | `feedback_soul_rebuild` | `engine._gated_feedback_soul_rebuild`（旧反馈批与统一兴趣更新线共用，写点与 trigger=`feedback_batch` 不变） |
| 1c | 确认假设攒批整份重建 | `hypotheses_soul_rebuild` | `engine._execute_pending_rebuild`（rebuild_pending 状态机） |
| — | P1 退役深层缓冲迁移（一次性） | `pipeline_deep_migration` | `pipeline.migrate_pipeline_deep_buffers`（构造时幂等运行） |
| 5 | 推测 promote/confirm/reject | `speculation_promote` / `speculation_confirm` / `speculation_reject` | `speculator`（引擎构造时 `attach_ledger`） |
| 6 | 12h 整理 应用 / 回滚 | `consolidation_apply` / `consolidation_revert` | `consolidator.run` / `consolidator.revert` |
| 7 | init 全量建像（偏好 + soul） | `init_preference_build` / `init_soul_build` | `engine.analyze_events` / `engine.build_initial_profile` |
| 8 | cognition sync（觉察/洞察 → soul） | `cognition_sync` | `cognition_cycle._sync_to_profile` |
| — | 对话结算（Phase 1） | `settle_speculation` / `settle_insight` / `settle_confusion` | `SoulEngine._apply_dialogue_settlement`（仅 worker；轻量 ref receipt，带 turn_id） |

> `init_soul_build` 是实现中发现的清单外写点（原 clist #7 只点名偏好写入），已一并挂钩。CLI 观测：`openbiliclaw ledger --line` / 按写点聚合 `openbiliclaw ledger`；shadow 门控采数（Phase 3）：`SELECT gate_verdict, COUNT(*) FROM profile_update_ledger WHERE gate_verdict LIKE 'shadow_%' GROUP BY 1`。

## 深层影响唯一模式（深层线归一，v0.3.178+）

深层画像（VALUES/CORE 层与 soul 层）的**事件驱动影响收敛为唯一模式**：**「假设（验证 confirmed）→ 攒批去抖 → 门控下 soul 重建」**。规格见 `docs/plans/2026-07-22-deep-line-consolidation-spec.md`。三条历史直写路径的处置：

- **P1 退役**：pipeline 不再消费 VALUES/CORE。`_BUFFERED_LAYERS` 摘除这两层；`FEEDBACK` 只路由 interest+surface；对话 `value/state` kind 在 pipeline 内失活（深层自述改走接入点①）。`update_layer(VALUES|CORE)` 封死为 no-op + WARNING（代码级封死，防止未来重新接线）。**一次性迁移**：`migrate_pipeline_deep_buffers` 在构造时幂等运行，把持久化 buffer 中残留的 VALUES/CORE 信号确定性转成 awareness note（内容前缀 `[migration:pipeline-deep]`，内容 hash 去重，marker + 台账行，清空旧键；崩溃重跑靠去重幂等）。
- **P2 补门控**：反馈批显著变化的整份重建此前**绕过所有门控**，现已接入接入点③（`feedback_soul_rebuild` 写点）；enforce downgrade/reject 会放弃本次重建。
- **P3 对话深层 candidates**（接入点①）：保留，行为不变。

**重建输入过滤**：所有 soul 重建（dialogue / feedback_batch / confirmed_hypotheses）经 `_rebuild_active_insights` 过滤，有两扇门进得来：①用户确认路径 `validated=True 且 confidence>=0.75`；②**行为挣来的自主资格**（2026-07-27，用户决策「给模型一些自由度」）——`_hypothesis_auto_validated`：置信度 ≥0.8 且创建 ≥7 天且证据 ≥3 条且用户从未裁决（`user_verdict==""`）。自主门槛每一项都严于确认路径，`rejected` 一票否决且永久（0.99 也不行），`confirmed` 走 ①。置信度 ≥0.95 走快速档：免 7 天资历等待，其余守卫（证据/未裁决/门控/拒绝否决）全部照旧。自主达标的假设由 `run_pending_rebuild_if_due` 开头的扫描标进**同一台** pending 状态机（同去抖 / 同门控 / 同台账，refs 前缀 `auto_hypothesis:`），幂等重扫不延长去抖。rejected/未达标假设对重建不可见，因此一次 reject 的下一次重建会把旧结论**挤出**。

**rebuild_pending 状态机**（`engine.py`，持久化于 `memory/rebuild_pending_state.json`）：`update_from_feedback` 是 confirm/reject 的单一入口，两者都置 `rebuild_pending {set_at, trigger_refs, retry_count}`。已有 pending 收到相同 trigger ref 时完全幂等，不写盘、不改变 `set_at/retry_count`，避免重复请求无限延长 debounce 或抹掉有界重试；只有未见过的新 trigger ref 才按「新证据重开」合并 refs、重置 retry 并重新置时。自主假设 ref 在首次入队时与 pending 原子写入 `auto_hypothesis_trigger_refs` 消费集，成功、拒绝或耗尽重试后都不会被后续扫描重新排队；门控 context 同时携带 `confirmed_hypotheses` 与 `auto_validated_hypotheses`，不会只看到哈希 ref。去抖 `_DEEP_REBUILD_DEBOUNCE_HOURS=6` 后由 12h 认知循环 / 下一次对话学习 / 反馈批触发门控重建（trigger=`confirmed_hypotheses`）。清标语义：门控 accept+重建成功→清标；真实 downgrade/reject（`is_error=False`）→清标 + 记 `last_gate_refusal`（本批放弃，新 confirm/reject 重开，无无限重试）；LLM/解析异常或重建异常（`is_error=True`）→保留 pending、`retry_count+1`，达 `_REBUILD_MAX_RETRIES=2` 后清标 + WARNING（有界）。构建期间释放锁允许并发 re-mark，用 `set_at` compare-and-swap 对账；重启后 `_rebuild_running` 复位、marker 持久化自动恢复。marker 写盘使用同目录 `.tmp`、`flush+fsync` 与原子替换；序列化或文件系统失败会 WARNING 并向上传播，临时文件在 `finally` 清理。Wave 2 不再保存 `seg_marker`/claim 进度；未 applied receipt 的显式同 ref retry 依赖 marker 的 set-union 幂等继续完成，稳定 effect 故障注入由 Task 2.3 覆盖。

## 画像更新逻辑详解

当前实现里，“画像更新”不是一次单点写文件，而是一条分层链路：

`事件/Event` → `偏好/Preference` → `觉察/Awareness` → `洞察/Insight` → `画像/SoulProfile`

但这条链路并不是每次都从底层一路跑到顶层。系统会根据信号类型、强度和累计程度，决定这次更新只停在偏好层，还是继续推进到 `SoulProfile` 重建。

### 如果只看最终 `SoulProfile` 本身，可以把它读成 3 个层次

很多人会把“画像”理解成一段自然语言描述，但当前 `SoulProfile` 实际上至少包含 3 层信息：

1. **总述层**
   这是最像“人物小传”的部分，回答“这个人大致是什么样的人”。
   主要字段：
   - `personality_portrait`
   - `core_traits`

2. **解释层**
   这是画像真正变得立体的部分，回答“他是怎么理解世界的、在被什么驱动、最近处于什么阶段”。
   主要字段：
   - `cognitive_style`
   - `motivational_drivers`
   - `current_phase`
   - `values`
   - `life_stage`
   - `deep_needs`

3. **上下文层**
   这层不是为了给用户直接读“人格总结”，而是为了让后续 LLM 和产品逻辑知道这个画像最近是基于什么上下文形成的。
   主要字段：
   - `preferences`
   - `recent_awareness`
   - `active_insights`

可以把它理解成：

- **总述层**：你是谁
- **解释层**：你为什么会这样
- **上下文层**：最近哪些证据在支撑这个判断

### 一个简单例子

如果系统最近对你的理解是“你不满足于知道结果，更想把结构看明白”，那么在 `SoulProfile` 里可能会长成这样：

- `personality_portrait`
  “这是一个会主动追问复杂问题底层逻辑的人，不太满足于结论本身，更在意因果链和结构感。”
- `core_traits`
  `["理性", "重结构", "谨慎"]`
- `cognitive_style`
  `["会先找框架", "喜欢把问题讲透", "对证据比较敏感"]`
- `motivational_drivers`
  `["建立判断确定性", "持续扩展理解边界"]`
- `current_phase`
  “最近更像在一边吸收高密度信息，一边整理自己的判断框架。”
- `preferences.top_interests`
  `国际时事 / 历史 / 纪录片`
- `recent_awareness`
  “最近连续浏览高信息密度国际议题内容”
- `active_insights`
  “用户可能在通过深度内容建立更稳定的判断框架”

所以最终画像并不只是那段 `personality_portrait`，而是一整组“总述 + 解释 + 上下文”的组合。

### 先说结论：哪些东西会真的影响画像

当前会进入画像更新链路的主要有 4 类信号：

- **行为事件**：`view / search / favorite / like / follow` 等，通常先更新偏好层
- **推荐反馈**：`like / dislike / comment / dismiss`，会先记事件，再按批量阈值决定是否重分析偏好和重建画像；其中 `comment` 是中性直接反馈，不预设正负向
- **聊天信号**：用户在对话里明确表达的 `interest / dislike / goal / value / state`
- **人工生成的中间理解**：`awareness` 和 `insight` 不直接改偏好，但会在画像重建时作为输入材料参与描述

真正持久化到“你是谁”的，是 `soul.json`；但驱动它变化的，不只是 `soul/` 自己，还包括 `memory/` 中的事件、反馈状态、聊天候选和认知更新文件。

### 1. 初始化画像：第一次把人“立起来”

首次初始化时，走的是 `SoulEngine.build_initial_profile(history)`：

1. 先读取已有 `preference` 层。
2. `openbiliclaw init` 已经先把 B 站历史 / 收藏 / 关注，以及显式启用的小红书、抖音、YouTube、知乎、Reddit、Linux.do、Bangumi、GitHub bootstrap signals 汇总成事件批次，调用 `analyze_events()` 更新偏好层。
3. 再加载历史 `awareness_notes` 和 `active_insights`。首次新装通常为空；如果第 2 步的初始化分片输出了临时 `awareness_candidates` / `insight_candidates`，`SoulEngine` 会把它们追加到本次 profile-build prompt 的 awareness / insights 输入中。
4. `ProfileBuilder.build()` 把 `history_summary + preference_summary + awareness + insights` 一起送给 LLM。临时 chunk cognition 只参与这次 prompt，不持久化到 awareness / insight 层。
5. LLM 返回结构化 JSON，必须包含：
   - `personality_portrait`
   - `core_traits`
   - `cognitive_style`
   - `motivational_drivers`
   - `current_phase`
   - `values`
   - `life_stage`
   - `deep_needs`
6. `ProfileBuilder` 校验字段完整性和画像长度，成功后才写入 `soul.json`。
7. `build_initial_profile()` 在 `soul.json` 写入完成后立即返回；这个返回点是 guided init 阶段 3 的严格提交屏障。正向兴趣猜测和避雷探针不属于“画像已生成”的必要条件，不再在本方法内同步 `force_tick()`，而由 init wrapper 完成或部分完成后恢复的 `RuntimeContext.restart_background_tasks()` one-shot 调度。因此阶段 4 的内容发现只能读取已经校验、持久化的完整画像，探针失败或维护流量被空库存暂停也不会反向拖住初始化画像。

小红书 bootstrap signals 的来源是浏览器插件在小红书页面中解析出的 notes，不是后端爬虫，也不是 Chrome 浏览器历史。scope 映射为：

| 小红书 scope | 事件类型 | 用途 |
|-------------|----------|------|
| `saved` | `favorite` | 高强度收藏/想回看信号 |
| `liked` | `like` | 中高强度偏好信号 |
| `xhs_history` | `view` | 小红书页面明确暴露时的浏览/足迹 state，强度较弱；普通推荐流不计入 |

抖音 bootstrap signals 的来源是浏览器插件在抖音页面中解析出的 videos / creators，不是后端爬虫，也不读取 Chrome 浏览器历史。scope 映射为：

| 抖音 scope | 事件类型 | 用途 |
|-----------|----------|------|
| `dy_post` | `view` | 用户自己发布内容，作为弱口味信号 |
| `dy_collect` | `favorite` | 收藏/想回看信号，强度最高 |
| `dy_like` | `like` | 中高强度偏好信号 |
| `dy_follow` | `follow` | 对创作者长期内容的兴趣信号 |

Linux.do bootstrap signals 来自扩展在真实 `linux.do` tab 中执行的同源只读 GET，不读取 Chrome 浏览器历史，也不上传 Cookie、CSRF 数据或原始响应。个人 scope 必须由 `/session/current.json` 正面确认当前用户；`_t` 只作为“可能已登录”的布尔提示。scope 映射为：

| Linux.do scope | 事件类型 | 用途 |
|----------------|----------|------|
| `linuxdo_bookmarks` | `favorite` | 用户明确保存、希望回看的主题 |
| `linuxdo_likes` | `like` | 中高强度偏好信号 |
| `linuxdo_read_history` | `view` | 站点账户阅读历史的弱偏好信号 |

GitHub bootstrap signals 由后端官方 REST client 读取公开 starred repositories。每条只产生 `favorite`，canonical identity 为 `github:repository:<numeric id>`，行为时间优先使用 star media type 的 `starred_at`；forks、watchers、open issues 不映射为 share/view/comment。该批次只在显式 init 或按需 fetch 产生，不能被描述成后台增量同步。

这里有两个重要约束：

- `personality_portrait` prompt 目标为 150-260 字，后端校验容忍 120-500 字；超出范围认为画像无效
- 如果 LLM 返回坏 JSON 或空内容，旧画像不会被覆盖；初始化大批量 history 触发风控 / 坏 JSON 时会移除原始标题和 context，用结构化偏好、来源分布、觉察和洞察重试一次
- 辅助字段（如 `motivational_drivers`、`values`、`deep_needs`）缺失或轻微格式不符时会补空值并记录 warning，避免真实 provider 少吐一个列表字段导致首次初始化失败

所以初始化不是“随便生成一段描述”，而是一次严格结构化的建档。

### 2. 行为事件路径：大多数变化先停在偏好层

日常行为事件先由 `MemoryManager.propagate_event()` 写入 SQLite 事件层。它当前只负责**落事实**，不会自动一路向上刷新五层。

初始化建档或手动 `rebuild-profile` 这类批量重建路径，才会由 `SoulEngine.analyze_events(events)` 直接触发偏好分析：

1. 读取当前 `preference` 层。
2. 调用 `PreferenceAnalyzer.analyze_events()`。
3. 里面会用 `build_preference_analysis_prompt()` 把：
   - 本批 `events`
   - `existing_preference`
   一起发给 LLM，提取结构化偏好。
4. 返回结果会进入 `merge_preferences()`，与旧偏好合并。
5. 合并后的偏好写回 `preference.json`。

初始化这类大批量事件会按分片并发分析，但初始 chunk fan-out 取 `min(16, LLMService.concurrency)`，一波处理完再推进下一波，避免拉全量历史时一次性创建所有 prompt 任务和等待队列，也避免一个请求触发 provider cooldown 后其余排队请求级联失败。prompt 超限或无效 JSON 触发的递归二分在每个顶层 chunk 内顺序处理左右两半，不会绕过 fan-out 再创建指数级排队子请求。同波任一任务硬失败时会显式 cancel + drain sibling，保证调用返回前没有遗留 provider coroutine。初始化 chunk 常规输出上限为 `PREFERENCE_CHUNK_MAX_TOKENS=4096`；该任务只抽取有界 JSON，在 provider 支持时显式关闭 reasoning，最终画像 prose 仍沿用 provider 默认。若兼容网关明确报告 reasoning 已耗尽 4096 tokens、`finish_reason=length` 且没有 final content，仅该 chunk 用普通结构化上限 16384 重试一次。临时 429 / cooldown 最多等待 65 秒重试两次，HTTP 402、余额不足或额度耗尽则立即失败。偏好分析的事件批次和 existing preference 已经完整放在 user prompt 中，因此单批 / 分片 LLM 调用会在 `LLMService` 支持时传 `inject_core_memory=False`，避免把动态 core memory 再拼进 system prompt、打穿 provider prompt-cache 前缀。初始化 chunk 的 LLM schema 还允许返回少量 `awareness_candidates` / `insight_candidates`：它们不是长期认知层产物，只是本轮初始画像的临时上下文；`SoulEngine.analyze_events()` 会从持久化 preference 中剥离私有 `_init_cognition_context`，随后 `build_initial_profile()` 一次性消费并清空。偏好分析还会在每次 LLM 调用前检查 prompt 体积：`event_chunk_size` 只是第一层按条数粗分片；如果某个 chunk 的 `system_instruction + user_input` 超过本地保守预算，`PreferenceAnalyzer` 会继续递归二分该 chunk。若单条事件本身过长，会只保留 `event_type / title / context / inferred_satisfaction / satisfaction_reason` 和 `metadata.source_platform / up_name / bvid / feedback_type / reaction / signal_strength / retracted / comment_text / comment_kind`（`_COMPACT_METADATA_KEYS`）等偏好提取关键字段——用户亲手写的评论 / 弹幕正文（`comment_text`，已在采集端截断 200 字符）是最强兴趣表达之一，随 compact 路径保留进 LLM，只截断长文本并丢弃 `raw_context`、字幕、原始 payload 等大字段。compact 后仍超预算的单条事件会被跳过并记录 warning，其他事件继续参与合并。

若某个分片被 LLM 风控拒绝或返回非 JSON，`PreferenceAnalyzer` 仍会递归拆小该分片；最终只有仍失败的单条事件会被跳过。若 provider 返回明确的 context-window 错误（例如 `n_keep >= n_ctx`、`context length`、`prompt is too long`），偏好分析会按同一套拆分 / compact 逻辑重试；临时限流走上述有界重试，认证、网络、余额不足、模型不存在等错误仍会让调用失败，避免把服务不可用伪装成成功。

`satisfaction_filter_enabled` 默认开启后，偏好分析会先把 `quick_exit` 等被动 negative 事件从 prompt 中移除，避免误把标题党点击学成兴趣。显式负反馈不走这条丢弃路径：`feedback_type=dislike` 或 `reaction=thumbs_down` 会保留在 prompt 里，但只能贡献 `disliked_topics`、风格避让或置信度下调，不能贡献正向 `interests` / `favorite_up_users`。`feedback_type=comment` 会被分类为 `neutral/direct_feedback`：它只表示“用户对推荐内容给了直接文字反馈”，PreferenceAnalyzer prompt 明确要求根据 `feedback_note` / 备注 / `context` 内容判断喜欢、不喜欢或中性说明，不能因为它是 comment 就默认当正向。

这一层真正做的不是“生成画像”，而是把近期行为压缩成结构化偏好状态，例如：

- `interests`
- `style.preferred_duration / depth_preference / humor_preference`
- `context.session_type`
- `exploration_openness`
- `disliked_topics`
- `favorite_up_users`

#### 偏好层合并规则

`PreferenceAnalyzer.merge_preferences()` 当前有几条很具体的规则：

- 兴趣按 `(name, category)` 作为唯一键合并
- 老兴趣会先做时间衰减：`weight × 0.9^weeks`
- 衰减后若低于 `0.05`，该兴趣会被丢弃
- 同名兴趣再次出现时：
  - `first_seen` 保留最早值
  - `last_seen` 更新到现在
  - `weight` 取旧值和新值的较大者
- `favorite_up_users` 走旧 ∪ 新集合并集累积，不会丢历史值（修正了此前「本批一旦提到任意创作者就整体替换历史列表」的 bug）
- `disliked_topics` 走**近因有序并集**：本轮避雷项排在前，与历史去重后再截到 `_DISLIKED_TOPICS_STORE_CAP`（128）。每轮被重新标记的雷点会冒到前面，长期不再出现的雷点滑出尾部衰减掉。下游 prompt 上限（discovery + 推荐摘要）与存储上限同为 128，存进来的避雷项全部进 LLM 画像输入，不再有任何截断（近因并集修复前的存量条目仍是字典序，任何小于存储上限的截断都会按码点而非相关性丢雷点）
- `style/context` 先继承默认值，再叠加旧状态，再叠加新状态

这意味着行为事件对画像的第一影响，通常不是直接改 `personality_portrait`，而是先慢慢把偏好层往一个更稳定的方向推。

普通浏览器事件、推荐点击和插件 bootstrap 结果共享 durable 增量路径：当 `soul_engine.is_profile_ready()` 为真时，生产者先给 event 标出 `profile_update_owner`，再通过 `EventIngressService` 只提交事实与 receipt；HTTP/source callback 仅 wake。app-owned `EventProcessingScheduler` 让 `profile_events` generic consumer 与 `content_feedback` consumer 分别按 durable cursor 分页扫描，按 event row ID 生成稳定 `ProfileSignal` ID，并通过 `ProfileUpdatePipeline.checkpointed_enqueue_batch()` 在同一 snapshot 中原子提交 buffer+cursor，随后调用 `tick_if_buffered()`。因此 commit 后丢 wake、scan 后崩溃或 checkpoint 后尚未 consume 都可由 5 秒扫描/启动恢复重做且不双计；空恢复不触发周期 cognition。owner cutover fence 阻止升级前 direct-ingest 行重学。retraction 的数据库折价是 generic claim 的前置投影，失败不推进 cursor；hypothesis/import feedback 由其它 owner 处理或只越过 content-feedback cursor。`pending_signal_events` 与 discovery 的 `last_processed_event_id` 仍只控制补货，不是画像 cursor。rejected/not_initialized 不落事实也不进入 pipeline；首次 init 自己拥有显式 build。知乎只有任务 payload 显式带 `profile_update=true` 才产生 generic-owned 画像事件；CLI 手动回填仍使用 `fetch-zhihu --write-memory` / `--rebuild-profile`。generic claim 同样消费 `hover` / `scroll` / `snapshot` / `reshuffle` / `pause` / `seek` 这类 context-only collector 行：durable 行保留、cursor 照常推进，但对应 `ProfileSignal` 不会进入 layer buffer，也不会交给 speculator；`search` 不在其中（它是真实意图信号），`view` 仍是画像证据。

### 3. 推荐反馈路径：分成“即时记住”和“批量学习”两档

推荐反馈是当前画像更新里最细的一条链。它不是每点一次 `like/dislike` 都立刻重建画像，而是分成两层处理。

卡片 like/dislike 属于可撤销的软信号，并由后台批处理学习。单次 dislike 不会直接把某个
主题永久写成硬屏蔽；需要确定性修正时，用户仍可主动前往原有画像页写入持久 override，
或在原有对话页用自由文本说明偏好。本 Issue 不在推荐区新增纠偏引导入口。

#### 第一层：即时认知更新，不重建画像

`record_immediate_feedback_cognition()` 处理的是单条强反馈，目的是让系统“先记住这件事”，但不马上改整张画像。

当前支持：

- `comment` 且有文字：写入一条中性的 `profile_shift` 风格 cognition card，提示后续结合评论内容判断喜欢 / 不喜欢 / 补充说明，不默认当成正向偏好
- `dislike`：写入一条 `dislike_added`
- `like`：写入一条 `interest_added`

它会生成这些字段并写进 `cognition_updates.json`：

- `summary`
- `context_line`
- `impact`
- `reasoning`
- `evidence`
- `source = "feedback"`
- `source_label = "推荐反馈"`
- `confidence`

这条路径的特征是：

- 很快，适合 UI 立刻展示“阿B 刚记住了什么”
- 会去重，避免同一 summary 重复写
- **不会**直接触发偏好重分析
- **不会**直接重建 `SoulProfile`

所以单条反馈的主要作用，是先形成一条“认知变化记录”，而不是立刻把人格描述大改一遍。

#### 第二层：批量学习，必要时重建画像

真正会动到偏好层和画像的是 `process_feedback_batch_if_needed()`。生产 API 入口不承担这项慢工作：`/api/feedback` 先提交 durable `feedback` 事件，再以第二个独立 commit 写推荐反馈 projection；两表不宣称原子。相同 `request_id` 的重试会验证 durable event payload，并补齐缺失的 recommendation projection。随后 app-owned `EventProcessingScheduler`（旧名 `FeedbackBatchScheduler` 仅为兼容 alias）对 generic 与 content-feedback owner 做短窗口 debounce / coalesce，HTTP 响应不等待 pipeline 锁或 LLM。服务启动时也会恢复 owner 一次，保证“事件已提交、进程在 wake 前退出”的行仍能恢复。

统一兴趣线显式关闭时，才执行旧反馈批线：读取 `feedback_state.json` 游标之后的 feedback，排除 retraction；满 3 条后瘦身输入、调用 `PreferenceAnalyzer.analyze_events()`、写回 preference，必要时用 `ProfileBuilder.build()` 重建 soul、生成 cognition updates，最后推进游标。它是应急回滚路径，不是默认数据流。

##### 默认统一兴趣线如何取代旧反馈批

`feedback_preference_overwrite` 已退役：默认开启统一兴趣线后不再写新行，历史行仍可由 `openbiliclaw ledger` 查询。写点清单中的接班人是 `pipeline_layer_update(source="feedback")`。`process_feedback_batch_if_needed()` 变成一层 shim（`soul/engine.py`），方法名及三个调用方的外部接口不变；CLI / OpenClaw 只在写本轮事件前新增同一个 cutover prepare：

- **默认（开关开）**：
  1. 在任何 v2 event-only 写入前调用 `prepare_feedback_owner_cutover()`。如果发现 v0.3.191 的 `unified_interest_line_migrated_at` 但 pipeline checkpoint 的 content-feedback `owner_version < 2`，就把 cursor fence 到此刻最大的 feedback row id，并把 owner version、cutover time、cutover event ID 与 cursor 一次原子发布到 `pipeline_state.json`；这些旧行已经被 v1 direct pipeline owner 学过，不能重放。查询使用 `query_events_since()` 后按 id 取最大值，不能用按 `created_at` 排序的 `query_events(limit=1)`。fresh install 没旧 marker 时只发布 v2 owner、不抬 cursor。`feedback_state.json` 从此只作 migration provenance / 兼容镜像，不能作为 owner 验收权威。
  2. 持续按 `pipeline_state.json.consumer_cursors.content_feedback` 查询所有新 feedback 行；`unified_interest_line_migrated_at` 只保留 rollout provenance，不再是一锤子迁移门。
  3. owner predicate 是 `event_type=feedback` + `feedback_type ∈ {like,dislike,comment,dismiss}` + `import_source` 为空。hypothesis feedback（没有 `feedback_type`）和 Bangumi/import snapshot 不构造信号但仍推进 cursor；它们已由各自对象结算 / guided init owner 学习。retraction 同样越过 feedback cursor，但 `/api/events` 的 live retraction 仍由 generic pipeline 处理折价。这样每个 feedback namespace 只有一个学习 owner。
  4. 每行用 `signal_from_feedback(..., signal_id=f"feedback-event-{row_id}")` 还原成 `SignalType.FEEDBACK`；durable 行必须有正整数 id。**不能用 `signals_from_events()`**，后者不会保留 FEEDBACK 的优先消费、dislike 归档、门控重建和 `source="feedback"` 台账语义。
  5. **调用 `pipeline.checkpointed_enqueue_batch()`，在同一个 `_ingest_lock` 和同一次原子 replace 中发布 buffer+cursor，再调用 `pipeline.tick_if_buffered()`**。signal ID 仍在当前持久 buffer 中去重。
     - snapshot replace 失败：内存 buffer 与 cursor 一起回滚，owner 抛错并重试，不进入消费。
     - checkpoint 后、consume 前崩溃：buffer 与 cursor 已共同持久化，启动恢复会继续消费。
     - `tick_if_buffered()` 的空恢复 pass 是 O(1) 且不跑 speculator/cognition；只有存在 durable signal 时才 drain 并运行随画像变化所需的维护。独立周期画像维护继续调用完整 `tick()`。
     - 层更新成功后先保存 drain 状态，再在 ingest lock 外运行维护；维护失败不会让已应用信号在重启后重放，慢维护也不会长时间占住 enqueue。
  6. 返回形状保留 `triggered / feedback_count / preference_updated / profile_rebuilt`，另加 `unified_interest_line: True`、`migrated_feedback_events`（兼容键，现表示本轮 claimed 行数）、`enqueued_feedback_events` 与 `preference_changed`。`feedback_count` 是 tick 前持久化 INTEREST buffer 中的 FEEDBACK 数；`preference_updated` 表示本轮实际消费过 INTEREST。
  7. held-replay 不在 shim 里重跑：统一线上它是反馈消费特权，只在 `_after_pipeline_feedback_interest` 中、且仅当 drain 的批确实含 FEEDBACK 时运行。
- **显式回退（开关关）**：走 `_process_feedback_batch_legacy` 并恢复旧写点；这是应急回滚路径，不是默认数据流。

#### 什么叫“变化明显”

当前 `_preference_changed_significantly()` 的判定很明确：

- 只看 `weight >= 0.6` 的高权重兴趣
- 如果旧偏好里没有高权重兴趣，而新偏好有，算明显变化
- 如果高权重兴趣集合的增删差异达到 `2` 个以上，算明显变化
- 如果同一个高权重兴趣的权重变化绝对值 `>= 0.2`，算明显变化
- 如果新增了至少 `1` 个 `disliked_topics`，算明显变化

只有满足这些条件，系统才会认为“这不是局部波动，而是值得重写画像的变化”。

### 4. 聊天学习路径：先记候选，再看是否够格进入长期画像

聊天信号的处理路径是 `learn_from_dialogue()`，它比反馈更保守，因为聊天里更容易出现一次性情绪或随口表达。

完整链路如下：

1. 先把这轮对话写成一条 `dialogue` 事件进事件层。
2. 调用 `DialogueInsightAnalyzer.extract()`。
3. LLM 从这轮对话里提取候选信号，限定在：
   - `interest`
   - `dislike`
   - `goal`
   - `value`
   - `state`
4. 每条候选都带：
   - `content`
   - `confidence`
   - `evidence`
5. 候选先和历史 `insight_candidates.json` 合并，不直接写进偏好层。

#### 候选如何合并

`_merge_insight_candidates()` 会按 `kind + content` 合并：

- 新候选首次出现时，创建一条记录
- 重复出现时：
  - `occurrences + 1`
  - `confidence` 取更高值
  - `evidence` 更新为最新非空值
  - `updated_at` 刷新

所以聊天学习不是“听见一次就信”，而是把聊天信号当作待确认的长期候选。

#### 哪些聊天候选会立刻出现在画像页上

有一条更轻的 UI 路径：`_record_immediate_dialogue_cognition()`。

如果候选满足即时展示条件，就会先生成一张 cognition card：

- `goal / dislike / interest / value` 要求 `confidence >= 0.8`
- `state` 更保守，要求 `confidence >= 0.9`

这一步只影响 `cognition_updates.json`，不等于正式改画像。

#### 哪些聊天候选会真正进入偏好层

要进入长期学习，候选必须满足 `_candidate_ready_for_learning()`：

- `applied == False`
- `confidence >= 0.8` 或 `occurrences >= 2`

也就是说，**单次非常明确的高置信聊天信号**，或**同一个方向至少重复出现两次**，都会被转成一条 `dialogue_insight` 事件，再送进 `PreferenceAnalyzer.analyze_events()`。

之后的流程和反馈批量学习相同：

1. 用这些合格候选更新偏好层
2. 比较偏好是否显著变化
3. 只有显著变化时才重建 `SoulProfile`
4. 生成 cognition updates
5. 把这些候选标记为 `applied = True`

### 5. 觉察层与洞察层：不直接触发重建，但会影响下次画像重建长什么样

`generate_awareness_note()` 和 `generate_insight()` 本身不做“显著变化判定”，也不直接调用重建画像。

它们的作用更像是**给下一次画像重建准备解释材料**：

- `AwarenessAnalyzer` 从最近事件里生成保守的观察笔记
- `InsightAnalyzer` 从 `awareness + preference + soul_profile` 里生成解释性假设

这些结果分别写进：

- `awareness.json`
- `insight.json`

当下一次 `build_initial_profile()` 或后续重建画像时，`ProfileBuilder.build()` 会把：

- `history_summary`
- `preference_summary`
- `recent_awareness`
- `active_insights`

一起喂给 LLM。

所以可以把它们理解为：**觉察层和洞察层不是更新闸门，而是画像重建时的“叙述素材层”**。它们决定画像写得是否更像“这个人怎么理解世界”，而不是只像一堆兴趣标签。

#### 认知调用的有界输入与持久历史边界

`PreferenceAnalyzer` 的自动预算回退不再用“完整请求总字符 × 事件比例”估算分片。完整请求里旧 preference 可能很大，但独立 chunk 请求按既有合并契约传 `existing_preference={}`；旧估算会因此把本可一次发送的事件误拆成多次。现在自动路径从每个剩余事件 offset 使用生产 prompt builder 渲染独立请求，并以二分查找贪心装入不超过 `max_prompt_chars` 的最大当前前缀；因此后段局部超长事件只由既有单条 compact recovery 处理，不会迫使其后的短事件沿用第一段固定宽度而产生额外调用。显式 `event_chunk_size`、provider 限流重试和 chunk 结果合并语义不变。

`CognitionCycle._run_insight()` 继续读取、合并并保存完整 `insight.json`，只对本轮 LLM prompt 建一个最多 40 条的确定性视图。选择器先为最新 8 条和最近 8 条 `validated` / `user_verdict` 锚点保底，再分别给当前相关性 16 个槽、重要性/多样性 8 个槽；重叠或不足的配额流入统一加权补位。总分由当前觉察/画像相关性 35%、源索引新近性 25%、用户裁决 20%、置信度/证据 15%、重复支持 5% 组成，不调用 embedding 或额外模型。

文本先做 NFKC、英文词/CJK bigram 的 provider-independent 特征化。同一 confirmed/rejected/unjudged 状态的近重复假设只竞争一个 **prompt 槽**，不同状态永不归并，避免新重述吞掉旧确认或否定；最终仍恢复原存储顺序。模型新输出始终与**完整历史**执行 `merge_insights()`，选择器不改写、摘要或删除持久行；任何 legacy 文本令选择器异常时，立即退回 Phase 3 的「最新 20 + judged 20」有界视图，不阻断 cognition cycle，也不会丢 verdict。

这两项都是 provider/tokenizer 无关的请求形状优化：不修改 system prompt、输出 schema、reasoning、max token ceiling 或保存格式。历史基线由 `scripts/replay_token_diet_phase3.py` 保持固定选择器复现；`scripts/replay_weighted_insight_context.py` 对固定窗口 A/A/A、加权窗口 B 和完整历史 F 做隐私安全真实门，只写 hash、计数、usage、匿名结构质量与 route，不持久化事件、画像、prompt、模型正文、URL 或凭据。2026-08-06 的 442 条生产快照中，加权视图保留 40 条（其中 24 条在固定窗口外），完整 prompt `48523 → 27725` provider token，节省 `42.86%`；相对固定 20 条只增加 `3.75%` prompt token。最终 B 为 0 repair、9/9 结构有效、0 重复，置信度/平均证据漂移均落在 A/A 噪声内，完整历史 merge 后仍为 444 条。

### 6. 画像重建时，LLM 实际拿到什么

真正重建画像时，走的是 `ProfileBuilder.build()` + `build_soul_profile_prompt()`。

system prompt 的核心约束是：

- 只能根据给定材料推断
- 必须输出严格 JSON
- 人格描述目标 150-260 字，后端校验容忍 120-500 字
- 先写“怎么处理信息”，再写“长期在找什么”，最后写“最近处于什么阶段”
- 不要把兴趣 topic 堆成画像主体

输入则包括四块：

- `history_summary`
- `preference_summary`
- `recent_awareness`
- `active_insights`

这意味着当前画像重建不是只看最近 3 条反馈，也不是只看几句聊天，而是把：

- 长期历史
- 最近行为聚合出的偏好
- 近期观察
- 解释性假设

一起当作“重新描述这个人”的上下文。

### 7. 认知变化是怎么生成的

除了 `soul.json` 本身，系统还会生成一条独立的“你最近被记住了什么”的轨迹，这就是 `cognition_updates.json`。

聚合路径的 cognition update 由 `_build_cognition_updates()` 生成，主要有三类：

- `interest_added`
  触发条件：新出现的兴趣不在旧偏好里，且 `weight >= 0.75`
- `dislike_added`
  触发条件：新出现的 `disliked_topics` 不在旧偏好里
- `profile_shift`
  触发条件：`_profile_shifted(previous_profile, current_profile)` 为真，也就是画像文本或关键列表字段发生变化

这些 update 会附带：

- `summary`
- `context_line`
- `impact`
- `reasoning`
- `evidence`
- `source` / `source_label`
- `confidence`

这层的定位很重要：它不是替代画像，而是补一条“这次为什么变了”的可读解释，方便前端展示最近的认知变化。

### 8. 哪些文件会被更新

一次完整的“画像相关更新”可能涉及这些文件：

- `data/memory/preference.json`
  保存结构化偏好层
- `data/memory/soul.json`
  保存最终画像
- `data/memory/awareness.json`
  保存近期观察
- `data/memory/insight.json`
  保存解释性假设
- `data/memory/feedback_state.json`
  保存反馈批处理 / 统一兴趣线的 durable 游标；`unified_interest_line_migrated_at` 仅作 v1 provenance，`feedback_owner_version` / `feedback_owner_cutover_at` 固化 v1 direct owner → v2 cursor owner 的升级边界
- `data/memory/insight_candidates.json`
  保存聊天候选长期信号
- `data/memory/cognition_updates.json`
  保存“最近记住了什么”的结构化变化记录

这也说明：当前画像更新是一个“主数据 + 中间状态 + 可解释回显”并存的体系，不是单文件覆盖。

### 9. 一个完整例子：从一句话到画像变化

假设你最近连续发生这些事情：

1. 看了 3 条“国际局势深度解读”
2. 搜索了“国际新闻 因果链”
3. 聊天里说“我想把国际新闻背后的结构看明白”
4. 对一条“浅层热点复读”点了 `dislike`
5. 又在另一轮聊天里再次提到“我现在更想看讲透逻辑的内容”

系统大致会这样处理：

1. `view/search/dialogue/feedback` 先全部落入事件层。
2. `analyze_events()` 把观看和搜索提炼成偏好层，例如：
   - `国际局势`
   - `历史`
   - 更高的 `depth_preference`
3. 单次 `dislike` 先生成一条即时 cognition card，告诉你“这类内容被记成避雷方向了”。
4. 第一轮聊天会生成一个候选 `goal` 或 `interest`，但因为只出现一次，还不会正式写进偏好层。
5. 第二轮相似聊天出现后，候选的 `occurrences` 到了 2，且 `confidence >= 0.8`，于是进入长期学习。
6. 聊天候选和反馈批量一起推动偏好层出现显著变化，例如：
   - 高权重兴趣新增/强化
   - `disliked_topics` 新增了“浅层热点复读”
7. `_preference_changed_significantly()` 返回真，触发画像重建。
8. 重建时，LLM 会同时看到：
   - 历史标题摘要
   - 当前偏好层
   - 近期 awareness
   - active insights
9. 新 `soul.json` 可能不只是说“喜欢国际新闻”，而会写成：
   - “这个人会主动追问复杂事件背后的结构，更偏好能把因果链讲透的高信息密度内容”
10. 同时生成一条或多条 cognition updates，告诉前端：
   - 新兴趣更明确了
   - 新避雷方向出现了
   - 画像整体发生了一次可见转向

### 10. 当前实现的边界

为了避免画像抖动过快，当前实现刻意保守：

- `propagate_event()` 只落事件；普通 `/api/events` 的增量画像由 API 层在 accepted 后显式喂给 `ProfileUpdatePipeline`，不会由 memory 层隐式触发全链路刷新
- 单条反馈只做即时认知记录，不直接重建画像
- 聊天信号必须高置信且重复出现，才能进入长期学习
- 画像重建必须跨过“显著变化阈值”
- `awareness` 和 `insight` 会影响画像内容，但不会独立触发重建

换句话说，系统当前追求的是：**先把“你最近说了什么、做了什么”记稳，再在足够证据累计后，谨慎地改写“你是谁”**。

## 公开 API

### SoulEngine

```python
from openbiliclaw.soul.engine import SoulEngine
from openbiliclaw.llm.service import module_overrides_from_config

engine = SoulEngine(
    llm=registry,
    memory=memory_manager,
    module_overrides=module_overrides_from_config(config),
)

# 分析事件批次 → 更新偏好层
await engine.analyze_events([
    {"event_type": "view", "title": "世界史解说"},
    {"event_type": "search", "title": "纪录片推荐"},
])
# 执行后 memory_manager.get_layer("preference").data 已更新并持久化

result = await engine.process_feedback_batch_if_needed()
# {
#   "triggered": True,
#   "feedback_count": 3,
#   "preference_updated": True,
#   "profile_rebuilt": True,
# }

learning = await engine.learn_from_dialogue(
    user_message="我最近更想把国际新闻背后的结构看明白。",
    assistant_reply="听起来你在追求一种能把复杂事件看清楚的框架。",
    session="cli",
)
# {
#   "event_logged": True,
#   "candidate_count": 1,
#   "preference_updated": False,
#   "profile_rebuilt": False,
# }

# API runtime 在组装 dispatcher 后绑定唯一 queue；公开 façade 只做 admission。
engine.bind_dialogue_settlement_queue(queue)
receipt = await engine.submit_hypothesis_settlement(
    ref="2d0a6ff1",
    hypothesis="用户重视原始研究",
    requested_verdict="reject",
    turn_id="card-42",
    source="card_action",
)
assert receipt["outcome"] in {"applied", "already_settled", "stale_anchor"}

await engine.submit_confusion_settlement(
    ref="7",
    requested_verdict="reject",
    note="chat_settle",
    turn_id="chat-43",
    source="chat",
)

# dispatcher / learn worker 内才可调用 engine._apply_*；
# 普通 chat speculation settle 只在当前 learn job 内直接 apply，不二次入队。

updates = memory_manager.load_cognition_updates()
# [
#   {
#     "kind": "interest_added",
#     "summary": "阿B 刚记下了你对《这视频讲透了中东局势》的评论。",
#     "context_line": "来自：《这视频讲透了中东局势》",
#     "impact": "画像里“喜欢高信息密度、有人文关怀的内容”这条偏好会更明确。",
#     "reasoning": "这次反馈不只是喜欢/不喜欢，而是主动说清了你在意的内容气质。",
#     "evidence": "你评论《这视频讲透了中东局势》时说：这个很好看，有创意，我很喜欢，还有一些不油腻的人文关怀",
#     "source": "feedback",
#     "source_label": "推荐反馈",
#     "expand_hint": "expandable",
#     "created_at": "2026-03-15T10:30:00",
#     "notified": False,
#     ...
#   }
# ]
```

### SocraticDialogue

```python
from zoneinfo import ZoneInfo

from openbiliclaw.soul.dialogue import DialogueLearningMode, SocraticDialogue

dialogue = SocraticDialogue(
    llm=None,
    soul_engine=engine,
    llm_service=service,
    session="cli",
    local_timezone=ZoneInfo("Asia/Shanghai"),
    learning_mode=DialogueLearningMode.QUEUED,
    settlement_queue=queue,
)

reply = await dialogue.respond(
    "我最近很喜欢看讲得很透的纪录片",
    session="webui",
)
# reply: "我猜你喜欢的是那种能慢慢展开逻辑的讲述方式..."

print(dialogue.history)  # [DialogueTurn(role="user", ...), DialogueTurn(role="agent", ...)]
dialogue.clear_history()
```

`respond()` 只在得到非空的真实回复后才追加 agent turn。学习所有权必须显式选择：
API runtime 使用 `queued`，在同一事件循环 turn 同步提交 typed `learn`，缺少 queue
会在调用 LLM 前报配置错误；`reply_only_test` 明确不学习；`legacy_direct` 只由
CLI/OpenClaw 两个兼容构造点使用，保留既有 detached direct learning，不加入
queue/guard。每个 `SocraticDialogue` 实例用独立异步锁串行执行完整 turn 事务，
普通回复与工具调用共享同一顺序；等待锁时取消不会改动历史，持锁期间的 LLM
异常、超时或取消只删除本轮临时 user turn 并原样重抛，失败内容不进入历史或长期学习。

`respond(..., session="")` 可逐请求覆盖 UI ownership 标签；认知 history 仍跨 session 共享。`local_timezone` 与测试用 `now_provider` 固定历史时间事实，公开 `format_dialogue_turn_timestamp(timestamp, local_timezone=...)` 将 SQLite 的无时区 UTC 或带 offset 时间统一渲染为 `[MM-DD HH:mm]`，不读取当前时钟。

### DialogueAnchorManager / ConfusionManager

```python
from openbiliclaw.soul.dialogue_anchor import (
    ENTRY_CONFUSION_PROMPT,
    DialogueAnchorManager,
)

anchor = anchor_manager.establish(
    kind="confusion",
    ref=str(confusion_id),
    origin_turn_id=question_turn_id,
    entry=ENTRY_CONFUSION_PROMPT,
)
snapshot = anchor_manager.snapshot()

terminal = confusion_manager.process_anchor_settlement(
    confusion_id,
    action="resolve",
    interpretation="real_interest",
    note="dialogue_anchor",
    turn_id=reply_turn_id,
    anchor_generation=anchor.generation,
)
```

`snapshot()` 暴露排队所需的 `anchor_kind/ref/generation` 完整三元组，避免 confusion 锚被默认解释成 hypothesis；队头开始处理先校验受理时冻结的完整值。LLM 返回后的首副作用由 `note_relation(..., expected_generation=...)` 在状态锁内 CAS，调用方必须消费返回值，不能解锁后再重读。`process_anchor_settlement()` 会先持久入队再从 FIFO 队头执行；返回 `None` 表示副作用失败且已留队，并非吞掉或结算成功。`retry_anchor_settlements()` 供同一锚处理器与 12h 恢复路径续跑，`pending_dialogue_replays()` 暴露 12h 恢复扫描；API durable completion 路径不得直接调用 `resolve()` / `defer()`。

新客户端的主动假设结算只能通过 durable 卡片 action 进入上述锚/仲裁链；画像/认知更新区不得直接调用 `SoulEngine.update_from_feedback()`。deprecated `POST /api/insights/feedback` 只作为旧客户端兼容层复用相同结算实现，并以 `source="legacy_endpoint"` 留台账。

### DialogueSettlementQueue / AnchorAdmissionRegistry（内部基础设施）

```python
from openbiliclaw.soul.dialogue_learn_queue import (
    DialogueJobKind,
    DialogueJobResult,
    DialogueSettlementQueue,
)


async def dispatch(job):
    return DialogueJobResult(outcome="completed")


queue = DialogueSettlementQueue(dispatch)
queue.start()
queue.submit(DialogueJobKind.LEARN, {"turn_id": "turn-1"})
result = await queue.submit_and_wait(
    DialogueJobKind.CARD_DEFER,
    {"turn_id": "card-1"},
)
await queue.shutdown()
```

`accepting` 表示 producer 当前是否仍可 admission；`ready_for_interactive_submission`
进一步要求没有 active job 且队列为空，供会立即改变锚/疑惑状态的 pending-open 入口做
无副作用 busy 判定。`pause_and_drain(timeout=...)` 在等待旧任务清空期间继续接受新 job，
只有 `join()` 完成后才在同一 event-loop turn 内原子切换为 paused；超时不会留下“停止
受理但旧任务仍在跑”的半切换状态。

`submit()` 是同步 admission API：单调 sequence、深拷贝 payload、exhaustive
anchor transition、owner reservation、冻结 snapshot 与 `put_nowait` 之间没有
`await`。owner resolve 只更新自己的 per-ref entry；较早 ref 的 builder 迟到完成
不能把无 target 的全局 latest snapshot 从更晚受理的 reservation 拉回旧 ref。
完成阶段优先使用 payload 的显式 target；targetless `learn` 则从 effective frozen
snapshot 推导 target，builder 则从 transition 推导 target，并在 follow-up 返回或抛错
后回读 durable actual state。这样 worker 内 `_apply_*` 释放的锚不会让 registry 保留
旧 generation；该刷新仍携带 completed sequence，只能更新自己的 per-ref head，不能让
`_latest_head_key` 越过更晚的同 ref 或跨 ref reservation 回拨。`submit_and_wait()` 只供
worker 外 producer 排队等 completion；actual worker、活跃 job 产生的任意层 child，
以及父 job 结束后仍存活的 detached child 重入 `submit()` / `submit_and_wait()` 都立即
抛 `DialogueSettlementReentryError`，不会 inline 调 dispatcher。worker 内嵌套结算由
同一 actual task 的 coroutine 调用链直接调用 `_apply_*`；`create_task()` child 只能
返回数据给父调用栈，不能获得写权。`anchor.establish`
只接受 `pending_probe_throw`、`pending_confusion_throw`、
`durable_confusion_ensure` 三个已声明 producer source，其他非空 source 也在
admission fail closed；`card.discuss` 与
`confusion.attribution.replay(needs_anchor=true)` 是当前完整 builder 集合；新增
builder kind 必须先扩 policy 与穷尽测试。进程退出会丢弃 registry，未执行 job 不做
durable 恢复。

> TODO（生产观测阈值）：持续观察 queue 的 `queue_wait_ms` / `run_ms`；当
> `202 ratio >1%` 或 `p95 >5s` 时才评估后续 analyze/apply 拆分。本 Wave 保持
> LLM 与 mutation 在线内串行，不抽 read-only DTO、不加 snapshot digest/CAS，
> 也不预埋第二队列。

### DialogueSettlementGuard（内部基础设施）

```python
import asyncio

from openbiliclaw.soul.dialogue_settlement_guard import (
    DialogueSettlementGuard,
    DialogueSettlementMutationOutsideWorker,
)

guard = DialogueSettlementGuard()
worker_task = asyncio.current_task()
assert worker_task is not None

with guard.dialogue_settlement_worker(worker_task):
    guard.require_dialogue_settlement_worker()  # 当前实际 worker 可写
```

`register_worker()` 分配 fresh nonce；`revoke_worker()` 与 `clear_if_current()` 都要求 task identity + nonce 精确匹配。`activate_worker()` 只携带 nonce，`require_dialogue_settlement_worker()` 始终同时比较 nonce 与 `asyncio.current_task() is registered_worker_task`；没有 delegated task 字段、inline 授权 context manager 或临时例外。runtime 热重载已接到 exact revoke / fresh reauthorize；`SoulEngine._apply_*`、anchor/confusion manager 与 API card façade 的 production protected mutator 均安装 guard。Wave 3 wiring gate 逐项证明所有声明入口只能由 actual worker mutation，endpoint、active child、detached stale child 与普通后台 task 都不能旁路。

### PreferenceAnalyzer

```python
from openbiliclaw.soul.preference_analyzer import (
    DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
    MAX_CONCURRENT_PREFERENCE_CHUNKS,
    PreferenceAnalyzer,
)

analyzer = PreferenceAnalyzer(
    registry=llm_registry,
    max_prompt_chars=24_000,  # 默认值：发送 LLM 前的保守 prompt 字符预算
)
updated_pref = await analyzer.analyze_events(
    events=[...],
    existing_preference=current_pref,
    event_chunk_size=DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,  # 默认初始化粗分片：200 条
)
# 初始化路径每波最多推进 200 * min(16, LLM service concurrency) 条事件；
# chunk 常规输出上限为 4096 tokens；reasoning-only length 仅重试一次 16384；
# 临时限流最多等待 65 秒重试两次，硬失败 cancel + drain 同波 sibling；
# 单个 chunk 超过 max_prompt_chars 时仍会继续按 prompt 预算拆小。
# 偏好提取的 user prompt 已含事件批次和 existing_preference；
# 使用 LLMService 时会关闭额外 core memory 注入，保护 provider prompt-cache 前缀。
# 初始化调用的 chunk response 还可能带 `_init_cognition_context` 私有键；
# SoulEngine 会在写 preference.json 前剥离，并只喂给紧接着的 profile build。
assert DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE * MAX_CONCURRENT_PREFERENCE_CHUNKS == 3200
# 返回:
# {
#   "interests": [{"name": "历史", "category": "知识", "weight": 0.82, ...}],
#   "style": {"preferred_duration": "long", "depth_preference": 0.91},
#   "exploration_openness": 0.66,
#   "favorite_up_users": ["小约翰可汗"],
#   "disliked_topics": ["低质标题党"],
# }
```

### 分类词表与一次性迁移

```python
from openbiliclaw.soul.category_migration import CategoryMigrator
from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB, resolve_category

# 一级分类闭集：19 项，含「其他」；不是 config，变更词表等同代码变更。
assert "其他" in CATEGORY_VOCAB

# 源头或运维工具可把任意分类收敛到词表：
category = await resolve_category("技术", embedding_service)
# category == "科技"；无 embedding 或相似度不足时返回 "其他"

migrator = CategoryMigrator(memory=memory_manager, llm_service=llm_service)
preview = await migrator.run(dry_run=True)
# preview.mapping: {"泛娱乐": "娱乐", "宠物": "萌宠", ...}

applied = await migrator.run(dry_run=False)
# applied.run_id 可通过 ProfileConsolidator.revert(applied.run_id) 回滚。
```

迁移校验是强约束：现存非空分类必须被映射恰好一次，目标必须逐字来自 `CATEGORY_VOCAB`；任一失败时不写 `preference.json`，也不产生 run 记录。已有词表内分类由代码强制恒等映射，避免 LLM 把干净分类改脏。

### 分类迁移与全量清理运维顺序

推荐顺序：

```bash
openbiliclaw profile-consolidate --migrate-categories
openbiliclaw profile-consolidate --migrate-categories --apply
openbiliclaw profile-consolidate --full
openbiliclaw profile-consolidate --full --apply
```

先做一级分类迁移，再做 `--full` 二级全量清理。迁移后，同名同类的精确重复会被阶段 0 规则层免费消化，能显著减少后续送 LLM 裁决的簇数；同名异类则保留为强制嫌疑簇，由带 `category` 的 judge payload 判断是同名异义（keep）还是误标（merge）。完成一次全量清理后，稳态交给 12h 定时任务：默认只看 likes top-512，配合输入 digest 与 no-merge 记忆，稳定画像不产生 LLM 调用。

### OnionProfile（五层洋葱模型）

```python
from openbiliclaw.soul.profile import (
    OnionProfile,
    CoreLayer,
    ValuesLayer,
    InterestLayer,
    RoleLayer,
    SurfaceLayer,
    MBTI,
    MBTIDimension,
)

# OnionProfile 包含五个内嵌层，从内到外：
# 1. CoreLayer - 最稳定的核心特质与深层需求
# 2. ValuesLayer - 价值观与内在驱动力
# 3. InterestLayer - 树形兴趣结构（domain → specifics）
# 4. RoleLayer - 生活阶段与当前处境
# 5. SurfaceLayer - 可观察的认知风格与内容偏好

profile = OnionProfile(
    core=CoreLayer(
        core_traits=["理性", "重结构"],
        deep_needs=["建立判断确定性"],
        mbti=MBTI(
            type="INTJ",
            dimensions={
                "EI": MBTIDimension(pole="I", strength=0.85),
                "SN": MBTIDimension(pole="N", strength=0.78),
                "TF": MBTIDimension(pole="T", strength=0.81),
                "JP": MBTIDimension(pole="J", strength=0.72),
            },
            confidence=0.72,
        ),
    ),
    values_layer=ValuesLayer(
        values=["理解本质", "逻辑严谨"],
        motivational_drivers=["追求确定性", "建立框架"],
    ),
    interest=InterestLayer(
        likes=[
            InterestDomain(
                domain="国际时事",
                weight=0.88,
                specifics=[
                    InterestSpecific(name="中东局势", weight=0.85),
                    InterestSpecific(name="欧洲政治", weight=0.80),
                    InterestSpecific(name="经济动向", weight=0.75),
                ],
            ),
            InterestDomain(
                domain="历史",
                weight=0.82,
                specifics=[
                    InterestSpecific(name="冷战历史", weight=0.80),
                ],
            ),
        ],
        dislikes=[
            InterestDomain(domain="浅层热点复读", weight=0.9),
            InterestDomain(domain="标题党", weight=0.85),
        ],
        favorite_up_users=["小约翰可汗", "不知所云"],
    ),
    role=RoleLayer(
        life_stage="职业早期，追求知识深度",
        current_phase="最近在系统地补齐国际事务背景知识",
    ),
    surface=SurfaceLayer(
        cognitive_style=[
            "会先找框架",
            "喜欢把问题讲透",
            "对证据比较敏感",
        ],
        exploration_openness=0.65,
    ),
    personality_portrait="这是一个会主动追问复杂问题底层逻辑的人...",
)

# 向后兼容垫片属性（支持旧代码渐进迁移）
assert profile.core_traits == profile.core.core_traits
assert profile.deep_needs == profile.core.deep_needs
assert profile.values == profile.values_layer.values
assert profile.motivational_drivers == profile.values_layer.motivational_drivers
assert profile.cognitive_style == profile.surface.cognitive_style
assert profile.life_stage == profile.role.life_stage
assert profile.current_phase == profile.role.current_phase

# 自动迁移：从旧版 SoulProfile (v1) 转换到新 OnionProfile (v2)
legacy_soul = SoulProfile.from_dict(old_v1_data)
onion = OnionProfile.from_legacy(legacy_soul)
assert onion.version == 2
assert onion.core_traits == legacy_soul.core_traits
```

### ProfileBuilder / OnionProfile 构建

```python
from openbiliclaw.soul.profile_builder import ProfileBuilder

builder = ProfileBuilder(registry=llm_registry)
profile = await builder.build(
    history=[
        {"title": "AI 工具实测", "author": "科技UP主"},
        {"title": "效率系统分享", "author": "知识UP主"},
    ],
    preference=current_pref,
    awareness_notes=[
        {
            "date": "2026-03-20",
            "observation": "最近更常停在高信息密度内容里。",
            "trend": "明显更偏向讲透结构而不是只看结论。",
        }
    ],
    active_insights=[
        {
            "hypothesis": "用户可能在通过深度内容建立判断确定性。",
            "confidence": 0.71,
        }
    ],
)
# 返回 OnionProfile，自动填充五层结构

assert 120 <= len(profile.personality_portrait) <= 500
assert len(profile.core_traits) >= 3
assert profile.core.mbti.type  # MBTI 现已包含
assert profile.values_layer.motivational_drivers
assert profile.role.current_phase
assert profile.interest.likes  # 树形兴趣结构
```

```python
profile = await engine.build_initial_profile(history=[...])
loaded = await engine.get_profile()
assert loaded.core.core_traits == profile.core.core_traits
```

### AwarenessAnalyzer / InsightAnalyzer

```python
from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

awareness = AwarenessAnalyzer(registry=llm_registry)
notes = await awareness.analyze(
    events=recent_events,
    preference=current_pref,
    soul_profile=current_soul,
)
# 兼容模型把数组包在 {"results": [...]} / {"items": [...]} 等对象里的 JSON mode 输出

insight = InsightAnalyzer(registry=llm_registry)
hypotheses = await insight.analyze(
    awareness_notes=notes,
    preference=current_pref,
    soul_profile=current_soul,
)
```

### DialogueInsightAnalyzer

```python
from openbiliclaw.soul.dialogue_insight_analyzer import DialogueInsightAnalyzer

analyzer = DialogueInsightAnalyzer(registry=llm_service)
candidates = await analyzer.extract(
    user_message="我其实更想知道国际事件背后的因果链。",
    assistant_reply="你像是在找一种更稳定的理解框架。",
    core_memory=memory.get_core_memory(),
)
# [
#   {
#     "kind": "goal",
#     "content": "想更系统地理解国际局势",
#     "confidence": 0.84,
#     "evidence": "用户明确表达想看清背后的因果链。"
#   }
# ]
```

### ToneProfile

```python
from openbiliclaw.soul.tone import build_tone_profile

tone = build_tone_profile(
    profile=current_profile,
    preference_summary=memory.get_core_memory()["preference_summary"],
    recent_feedback=[
        {"feedback_type": "dislike", "feedback_note": "太油了"},
        {"feedback_type": "dislike", "feedback_note": "话有点满"},
    ],
)
# {
#   "density": "dense",
#   "warmth": "companion",
#   "playfulness": "medium",
#   "directness": "soft",
# }
```

## 设计决策

1. **偏好提取用 json_mode**：确保 LLM 返回结构化 JSON，便于程序处理
2. **标量分类不用 json_mode**：兴趣探针聊天情绪只需要 `strong_positive / weak_positive / neutral / negative` 单词，走普通文本调用；只有真正返回 JSON 的任务才启用 structured task
3. **对话事务按实例串行**：`SocraticDialogue` 用实例级异步锁覆盖 user turn 暂存、普通/工具 LLM 调用、agent turn 提交或回滚以及学习任务调度；异常与取消只回滚自己的临时 turn 并透明重抛，API / CLI / OpenClaw 公共边界再转换为安全中文错因
4. **`_build_service()` 回退**：未注入 LLMService 时从 SoulEngine 自动构建
5. **历史格式转换**：`agent` → `assistant` 角色映射，适配 OpenAI 消息格式
6. **画像生成独立为 `ProfileBuilder`**：避免把 prompt/JSON 校验逻辑塞进 `SoulEngine`
7. **认知变化解释由 soul 层生成**：`impact / reasoning / evidence` 都在后端认知链路里一次性产出，前端只负责展示，不在 UI 层脑补推理
8. **默认态上下文也由 soul 层负责**：`context_line / source_label / expand_hint` 由后端统一生成，保证“这是对哪条内容或哪组信号的判断”与详情口径一致
9. **评论型认知必须带内容上下文**：用户对“这条内容”的评论如果不带标题，认知卡片会失去可读性，因此即时反馈路径优先把标题写进 `summary`、`context_line` 和 `evidence`
10. **聚合判断宁可保守也不伪造对象**：拿不到可信标题时，回退为“基于最近几条相关内容”，避免看起来丰富但实际不准
11. **灵魂层失败不覆盖旧画像**：坏 JSON、空响应、缺字段时直接报错，已有 `soul.json` 保留
12. **觉察层保守去重**：同日 observation 标准化后相同则跳过，避免流水账堆积
13. **洞察层按假设文本合并**：相同 hypothesis 合并 evidence，confidence 取较高值
14. **验证状态只由代码更新**：LLM 只生成 hypothesis/evidence/confidence，`validated` 不信任模型输出
15. **反馈达到阈值后再学习**：默认累计 3 条新反馈才触发偏好重分析，避免单次噪声反馈频繁扰动画像
16. **画像重建走显著变化阈值**：只有高权重兴趣明显变化或新增 `disliked_topics` 时才重建 `SoulProfile`
17. **聊天信号受控生效**：聊天先落 `dialogue` 事件和 `insight_candidates.json`，高置信度候选或重复出现的候选才会进入偏好更新
18. **语气不单独持久化**：`ToneProfile` 是从画像、偏好和近期反馈实时推断出的派生层，避免把易调参的表达风格绑死在 `soul.json`
19. **“老B友”是基础人格，不是固定模板**：聊天、推荐和画像总结共用同一套语气维度，但会随着用户画像和近期反馈在信息密度、温度、梗感和直给程度上细调
20. **认知变化只在关键时刻生成**：只有新增高权重兴趣、明确避雷方向或画像明显转向时，才会形成 `cognition update`，避免把普通波动都做成提醒
21. **账户同步只补事件，不单独改画像**：history / favorites / following 统一先转成事件，再复用现有偏好分析与画像更新链，避免出现第二套理解逻辑
22. **画像先写“怎么理解世界”，再写“看了什么”**：`personality_portrait` 必须先围绕认知风格、驱动力和当前阶段组织，兴趣 topic 最多只作为少量证据出现，避免退化成偏好标签润色稿

## 假设置信度与用户判断（2026-07-26+）

`InsightHypothesis` 新增 `user_verdict ∈ {"", "confirmed", "rejected"}`，与 `validated` 分工不同：
`validated` 表示「当作真的用」，`user_verdict` 记录「用户是否**表过态**」。旧数据缺该字段按 `""`（从未评价）加载，
反序列化对取值做白名单，非法值一律回落 `""`。

`InsightAnalyzer.merge_insights` 的置信度合并规则（`_merge_confidence`）：

| user_verdict | 合并方式 | 理由 |
|---|---|---|
| `rejected` | `min(旧, 新)` | 用户说过「不准」。后续一轮分析是**同一个模型重读同类行为**，不能把分数谈回去；仍允许继续走低 |
| `confirmed` | `max(旧, 新)` | 用户说过「准」，一次弱分析不该把它打回确认下限之下；仍可继续升高 |
| `""` | 采用**最新**分析值 | 双向跟随最新证据 |

**修复的原始缺陷**：此前一律 `max(旧, 新)` 且 reject 只把分数压到 ≤0.35、不留任何「被否定过」的痕迹。
于是下一轮 12h 洞察提炼若再次给出同一条假设并打 0.8，`max(0.35, 0.8)` 就把用户的否定**完全抹掉**，
该假设还会重新越过待聊列表阈值（0.60）去问用户一件他已经否定过的事。同时置信度只增不减——
一条假设一旦某次被打高分就永久保持高位，无论后续行为如何变化。

对话中由用户自己给出措辞的修正版假设（`_persist_anchor_derived_hypotheses`）同样记 `user_verdict="confirmed"`。

**未改动**：深层重建的准入仍是 `validated AND confidence >= _REBUILD_MIN_CONFIDENCE(0.75)` 的与门——
事件只能影响置信度，给不了 `validated`，因此「事件自动下沉深层」依然不成立（深层线归一的边界未变）。
