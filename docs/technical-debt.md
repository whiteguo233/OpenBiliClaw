# 技术债清单

> 本文档只记录已经确认会影响长期可靠性、成本或可维护性的技术债。
> 普通 TODO、历史计划里的占位项和已经修复的债务不直接进入“当前已确认技术债”，
> 但会在“待确认线索”里保留索引，方便后续判断是否需要升级。

更新时间：2026-06-17

---

## 当前已确认技术债

### TD-001：串行化画像写入

**状态**：Open

**影响范围**：`preference.json`、`soul.json`、`cognition_updates.json`

**问题**

多条画像更新路径可能并发读旧状态，然后整体覆盖写回：

- `SoulEngine.learn_from_dialogue()`
- `SoulEngine.process_feedback_batch_if_needed()`
- `ProfileUpdatePipeline`
- profile consolidation
- dislike writeback

这些路径目前缺少统一的 profile mutation queue / lock，也没有在提交前基于最新
`preference.json` / `soul.json` 做 rebase / merge。高并发或后台任务重叠时可能出现
last-write-wins，丢失刚新增的兴趣、避雷方向或 cognition update。

**风险**

- 用户刚反馈的避雷方向被另一条后台画像写入覆盖。
- 聊天学习刚新增的兴趣被反馈批处理覆盖。
- UI 展示的“阿B 最近新记住了什么”和实际画像状态不一致。

**建议方向**

- 引入统一 profile mutation queue，所有写 `preference/soul/cognition` 的路径串行执行。
- 或者为每次写入增加提交前 rebase：重新读取最新文件，将本次变更 merge 后再保存。
- 为并发路径补端到端回归测试，覆盖 dialogue learning 与 feedback batch 同时写入。

---

### TD-002：限制 Soul 重建时 awareness / insight 输入体积

**状态**：Open

**影响范围**：`ProfileBuilder.build()`、`build_soul_profile_prompt()`、`awareness.json`、`insight.json`

**问题**

当前 `ProfileBuilder.build()` 会把 `_load_awareness_notes()` 和 `_load_insights()`
的全量 JSON 传入 `build_soul_profile_prompt()`。认知周期同步到 `soul.json` 快照时只保留：

- 最近 8 条 awareness
- 最近 6 条 insight

但画像重建 prompt 没有复用该窗口，也没有 prompt-size guard。随着
`awareness.json` / `insight.json` 长期增长，Soul 重建上下文可能越来越长。

**风险**

- Profile build prompt 超过模型上下文，导致画像重建失败。
- 大量旧 awareness / insight 稀释最新有效信号，让画像重建不够贴近当前状态。
- 重建成本随时间线性上涨。

**建议方向**

- 重建 Soul 时对 awareness / insight 做确定性裁剪，例如：
  - 最近窗口优先；
  - validated / high-confidence insight 优先；
  - 保留少量长期高价值洞察。
- 对旧 awareness / insight 先生成 compact cognition summary，再作为长期摘要输入。
- 增加 prompt-size guard；超过预算时先压缩 cognition 输入，而不是只 compact history。
- 增加回归测试，构造大量 awareness / insight，断言 profile build prompt 仍在预算内。

---

### TD-003：跨平台行为事件收集未形成统一 Soul 闭环

**状态**：Open

**影响范围**：`AccountSyncService`、浏览器插件 `BEHAVIOR_EVENT`、`/api/events`、跨平台 `bootstrap_profile` 与 runtime producers

**问题**

当前“行为事件 → Soul 维护”的通路不统一：

- 定时账号侧同步只有 B 站具备账号侧行为拉取入口：历史 / 收藏 / 关注拉取后会落事件并调用 `soul_engine.analyze_events()`，但已有画像后只更新 preference 层，完整 Soul 写回问题见 TD-005。
- 小红书 / 抖音 / YouTube / X 当前没有持续定时拉取账号点赞、收藏、浏览历史并作为 Soul 输入的统一 account sync loop；相关能力主要存在于初始化 / `bootstrap_profile` 任务、手动 fetch 或 discovery producer 中。
- 浏览器插件实时 `BEHAVIOR_EVENT` 会经 `/api/events` 落库并触发 refresh / activity 通知，但该入口没有把事件转成 `ProfileSignal` 或调用 `soul_engine.analyze_events()`；因此它更像行为日志和补货触发器，不是实时画像学习入口。
- Douyin / YouTube 插件内容脚本当前主要执行任务抓取，未接入通用实时行为 collector。

**风险**

- 用户以为“所有平台点赞 / 收藏 / 浏览都会持续影响 Soul”，但实际只有部分来源进入画像更新。
- 跨平台用户偏好长期偏向 B 站和初始化样本，外站后续行为难以持续修正画像。
- `/api/events` 已接入 `ProfileUpdatePipeline`，但仍需要继续梳理各平台实时事件、bootstrap 任务和账号同步之间的画像更新边界。
- 不同平台的“账号同步、实时采集、发现补池、初始化 bootstrap”边界不清，后续扩平台时容易重复造入口或漏接 Soul。

**建议方向**

- 明确定义事件分层：账号行为同步、实时页面行为、初始化 bootstrap、发现候选补池四类入口分别是否影响 Soul。
- 为小红书 / 抖音 / YouTube / X 设计统一 account sync abstraction，支持可配置周期、游标、去重和平台能力降级。
- 继续把实时插件事件、bootstrap 任务和账号同步的画像更新契约写成统一矩阵，明确哪些入口进入 `ProfileUpdatePipeline`、哪些入口只产生发现候选或任务结果。
- 为 Douyin / YouTube 评估接入通用 `startCollector()` 的可行性，或明确只支持任务型采集。
- 补端到端测试，分别断言：B 站 account sync 目前更新到哪一层；外站 account sync 未实现前不会被误报为已启用；`/api/events` 的实时事件是否进入 pipeline 与产品定义一致。

---

### TD-004：ProfileUpdatePipeline 未成为真正的画像更新单入口

**状态**：Open

**影响范围**：`ProfileUpdatePipeline`、`SoulEngine.analyze_events()`、`SoulEngine.learn_from_dialogue()`、`SoulEngine.process_feedback_batch_if_needed()`、source task ingest、推荐点击反馈

**问题**

`src/openbiliclaw/soul/pipeline.py` 的模块说明把 `ProfileUpdatePipeline.ingest()` 定义为所有画像影响信号的单入口，但当前生产路径并未完全收敛：

- source task 结果会经 `_ingest_profile_update_events()` 转成 `signals_from_events()` 后进入 pipeline。
- 推荐卡点击会直接调用 `pipeline.ingest(signal_from_recommendation_click(...))`。
- B 站 account sync 仍走 `SoulEngine.analyze_events()`。
- 聊天学习仍在 `SoulEngine.learn_from_dialogue()` 内部直接调用 `PreferenceAnalyzer` 和 profile rebuild。
- 推荐反馈批处理仍走 `SoulEngine.process_feedback_batch_if_needed()`。
- `/api/events` 实时行为入口只落事件和触发 refresh / activity 通知，不进 pipeline。

因此 pipeline 是“部分新入口”，不是当前真实的统一画像更新总线。

**风险**

- 同一种行为信号在不同入口下使用不同阈值、缓冲、强信号规则和 profile rebuild 规则。
- `signals_from_account_sync()`、`signals_from_dialogue()` 等适配器存在，但生产入口未统一使用，后续开发容易以为已经全量接线。
- 画像更新的观测口径分裂：pipeline state 看不到全部 Soul 变化，旧路径也看不到 pipeline buffer / tick 状态。
- 修并发写入、事件去重、信号回放时需要同时改多条路径，容易漏。

**建议方向**

- 明确一个迁移目标：所有 profile-affecting signal 先转成 `ProfileSignal`，再统一进入 pipeline；或反过来，把 pipeline 明确降级为“增量信号管道”，并更新文档和命名。
- 将 account sync、聊天候选、反馈批处理逐步迁移到 pipeline 的 signal adapter，保留必要的强信号 bypass 规则。
- 为每条入口补 contract test：输入事件后断言进入同一个 pipeline / mutation queue，并记录一致的层级更新结果。
- 在迁移完成前，把 pipeline 文档中的“All behavioral events...”改成当前真实状态，避免误导。

---

### TD-005：B 站 account sync 已有画像后只更新 preference，不完整维护 Soul

**状态**：Open

**影响范围**：`AccountSyncService`、`SoulEngine.analyze_events()`、`preference.json`、`soul.json`、`soul_profile.json`

**问题**

`AccountSyncService.sync_once()` 在拉到 B 站历史 / 收藏 / 关注变化后会：

- 调用 `memory_manager.propagate_event()` 落行为事件；
- 调用 `soul_engine.analyze_events(events)` 更新 preference 层；
- 只有在画像尚未初始化时才触发 `_auto_bootstrap_soul_profile()`。

而 `SoulEngine.analyze_events()` 当前只写 `preference.json`，不会重建 `soul.json`，也不会调用 `sync_profile_files()`。因此已有画像后，定时账号同步实际只把新行为沉淀到偏好层，不会立即改变 raw Soul / OnionProfile。

**风险**

- 用户以为“定时同步 B 站行为会持续维护 Soul”，但 UI / 推荐读到的 raw Soul 可能滞后。
- preference 与 soul 快照长期不一致，尤其是兴趣、避雷和 style 发生变化时。
- 后续排查“为什么账号同步了但画像没变”时，日志显示 `analyze_events done`，但实际没有 profile rebuild。
- `signals_from_account_sync()` 已存在但未在生产路径使用，迁移状态容易被误判。

**建议方向**

- 决定 account sync 的产品语义：只更新 preference，还是满足阈值后更新 Soul。
- 若应影响 Soul，把 account sync 事件转成 `ACCOUNT_SNAPSHOT` / `ENGAGEMENT_EVENT` signal 进入 pipeline，并按阈值更新 Interest / Surface / deeper layers。
- 若暂时只更新 preference，在 API 状态、文档和架构图中明确标注“账号同步只更新偏好层”，避免称为完整 Soul 维护。
- 增加回归测试：已有 `soul.json` 时执行 account sync，断言 preference / soul / mirror 的变化边界符合预期。

---

### TD-006：聊天学习后台任务未接入统一任务注册与关闭治理

**状态**：Open

**影响范围**：`SocraticDialogue.respond()`、`SoulEngine.learn_from_dialogue()`、`BackgroundTaskRegistry`、热重载 / 关闭流程

**问题**

`SocraticDialogue.respond()` 在生成回复后用裸 `asyncio.create_task()` 启动 `_background_learn()`。这个任务没有接入 runtime 的 `BackgroundTaskRegistry`，也没有用户可见的完成状态或重试机制。

**风险**

- 热重载、进程关闭或事件循环取消时，聊天学习可能尚未完成就丢失。
- 后台学习失败只写日志，用户侧无法知道“这轮聊天没有被记住”。
- 任务不在 registry 统计中，排查 hot reload 残留任务和关闭超时时容易漏掉。
- 对话学习与反馈批处理 / pipeline tick 同时写画像时，仍会放大 TD-001 的并发写入风险。

**建议方向**

- 让对话学习任务通过 `BackgroundTaskRegistry.track()` 或统一 runtime job queue 调度。
- 为聊天学习增加最小状态记录：queued / running / applied / failed，供调试和 UI 提示使用。
- 关闭 / 热重载时等待或取消该类任务，并记录未完成任务数量。
- 增加测试覆盖：对话响应后任务被 registry 追踪，异常不会吞掉状态，关闭流程可正确取消或 drain。

---

### TD-007：聊天 insight 候选合并依赖精确字符串匹配

**状态**：Open

**影响范围**：`insight_candidates.json`、`SoulEngine.learn_from_dialogue()`、`_merge_insight_candidates()`、聊天学习阈值

**问题**

聊天学习会先把 LLM 提取出的候选写入 `insight_candidates.json`，再根据 `confidence >= 0.8` 或 `occurrences >= 2` 决定是否进入长期学习。但 `_merge_insight_candidates()` 目前只按 `kind + normalized content` 合并，近义表达、同一偏好的不同措辞不会累计 occurrences。

**风险**

- 用户反复表达同一长期偏好，但因措辞不同被拆成多条候选，难以达到重复出现门槛。
- 高相似候选堆积，`insight_candidates.json` 噪声增加。
- `applied` 标记只作用于精确候选，语义重复候选可能后续又被学习一次。
- 聊天学习的稳定性过度依赖 LLM 每次输出完全一致的短句。

**建议方向**

- 对候选合并增加语义相似度判断，优先复用已有 embedding 服务或 lightweight similarity。
- 为候选增加 canonical key / normalized intent，减少同义内容重复。
- 合并时保留多条 evidence，而不是只用最新非空 evidence 覆盖。
- 增加测试：同一偏好的不同措辞能累计 occurrences；已 applied 的近义候选不会重复写入 preference。

---

### TD-008：旧 awareness / insight 公开入口仍保留固定窗口语义

**状态**：Open

**影响范围**：`SoulEngine.generate_awareness_note()`、`SoulEngine.generate_insight()`、`CognitionCycle`、Soul 模块文档

**问题**

当前主运行时已经通过 `CognitionCycle` 和 pipeline tick 管理 awareness / insight 的增量生命周期，但 `SoulEngine.generate_awareness_note()` / `generate_insight()` 仍作为公开方法保留：

- `generate_awareness_note()` 固定读取最近 50 条事件；
- `generate_insight()` 读取当前 awareness 列表生成 insight；
- 两者不直接触发画像重建，只为下一次 profile rebuild 准备材料。

这些方法在测试和文档中仍被标为已实现能力，容易让后续开发把它们当成当前主路径。

**风险**

- 新调用方绕过 `CognitionCycle` 的游标 / 分批 / 生命周期治理，重新引入固定窗口重复处理或漏处理。
- 文档中“生成 awareness / insight”的公开 API 与真实 runtime 主路径不一致。
- 维护者误以为调用这两个方法会立即更新 Soul 画像，但实际只更新中间层。

**建议方向**

- 确认这两个方法是否仍需要作为公开 API；若不需要，标注 deprecated 或删除。
- 若需要保留，改为委托 `CognitionCycle` 的同一套游标增量逻辑，避免双语义。
- 更新 `docs/modules/soul.md`，把它们从主能力入口降级为 legacy / diagnostic helper，或明确“不直接改画像”。
- 增加测试防止生产路径再次直接调用固定窗口入口。

---

## 待确认线索

以下项目是仓库中仍能搜到的 TODO / debt 线索。它们不一定都是当前有效技术债，
后续需要先确认生产路径是否仍使用，再决定是否升级为正式 TD 编号。

### `src/openbiliclaw/agent/orchestrator.py`

该文件仍有一组初始化、发现、反馈、对话和关闭流程 TODO。看起来更像早期 agent
shell 的占位实现；当前主要运行时已迁移到 CLI / API / runtime controller /
SoulEngine 组合。需要确认该 orchestrator 是否仍有生产入口。若无生产入口，可考虑删除
或标注 deprecated；若仍是目标接口，则应拆成独立实施任务。

### `src/openbiliclaw/memory/manager.py`

`propagate_event()` 附近仍有“是否触发 preference / awareness / soul 层更新”的 TODO，
另有 `top_down_reinterpret()` 未实现。当前实际更新主要由 `SoulEngine`、
`ProfileUpdatePipeline` 和 `CognitionCycle` 驱动，因此这些 TODO 可能是旧架构遗留。
需要确认是否保留为未来顶层重解释能力，或删除以减少误导。

### `src/openbiliclaw/soul/dialogue.py`

`SocraticDialogue.extract_insights()` 仍是 TODO，但当前对话理解已经由
`DialogueInsightAnalyzer` 和 `SoulEngine.learn_from_dialogue()` 接管。该方法可能是旧接口遗留；
建议确认是否还有调用方，没有则删除或改成委托新 analyzer。

### `src/openbiliclaw/recommendation/engine.py`

仍有 “Use LLM to create a personal topic narrative” TODO。该项属于推荐文案 / 主题叙事增强，
不是当前画像可靠性问题。若产品上仍需要，可作为 recommendation 模块体验增强任务单独规划。

### `src/openbiliclaw/eval/agents.py`

评估说明里提到 `layer_updaters._update_role/values/core()` 当前可能仍是 TODO / no-op。
需要用当前代码确认这些 updater 在生产管线中的真实状态；如果仍未生效，可能会影响五层画像
从信号层向 Role / Values / Core 的增量更新能力。

---

## 已修复债务索引

- 觉察/洞察认知链生命周期治理：v0.3.125 已补齐洞察反馈软作废接线，并将 awareness / insight
  生成从固定窗口改为游标增量取数。详见 `docs/changelog.md` 的
  “觉察/洞察认知链补齐生命周期管理”条目。
