# 推荐导演：Jev 逐对编排、双速修正与纯 Agent 模式

日期：2026-09-26。状态：**架构设计增补，尚未实现；不是已上线功能或可直接使用的配置**。

本文落实用户的新要求：每次选择两张卡、逐对拼成一屏；支持 Jev 独立编排、Agent 预编排后由 Jev 局部修正、纯 Agent 预编排；Jev 同时是可供过滤使用的独立 provider。

前置文档：[导演基础规格](2026-08-31-recommendation-director-spec.md)、[Jev 官方能力核实](2026-09-26-jev-provider-research.md)。本文覆盖与三模式有关的产品行为和接口设计；实现前仍需补齐版本化 schema、配置解析与迁移测试，不能直接沿用基础规格的单模式 JSON 合同。

## 1. 核心决定：把“导演”与“某一种模型”拆开

导演的目标是**组织一段有意图、能随真实反馈改变的内容体验**，而不是逐条预测点击，也不只是修改 ranker 权重。

- **Agent** 擅长构思未来几屏的主题路径、节奏与具体内容搭配，工作在慢路径。
- **Jev** 在给定上下文与有限备选中作局部判断，工作在快路径。
- **程序** 拥有候选、约束、状态、版本、幂等、提交和回退；模型只有建议权。

“快/慢”是职责与调度分工，不是经过实测的性能保证。三模式共享同一套提交和反馈基础设施，不建三套推荐系统。

| 编排策略 | 谁构思接下来几屏 | 谁选择当前屏的具体卡片 | 真实反馈后的调整 |
| --- | --- | --- | --- |
| `jev_recurrent` | 不依赖 Agent 计划；从会话意图与注册规则取得当前目标 | Jev 每次选择一对，逐对组成当前屏 | 更新应用状态，再生成或修正未提交草稿 |
| `agent_jev` | Agent 提前拟好未来几屏的意图和暂定卡片 | 正常保留 Agent 草稿；Jev 检查并局部换卡 | 小偏差换一张或一对；整体方向失效才请求 Agent 重规划 |
| `agent_only` | Agent 提前拟好未来几屏的意图和暂定卡片 | Agent 草稿经程序验证后执行 | 程序先落实明确禁令；Agent 异步重规划，来不及则走确定性回退 |

三种模式之外始终保留现有规则推荐作为 baseline/fallback。`off / shadow / enforce` 仍是灰度状态，不是第四、第五、第六种编排策略。

过滤独立配置。因此 `agent_only + Jev 过滤` 与 `agent_jev + 现有过滤` 都合法。“纯 Agent”仅指编排路径不调用 Jev，不等于整个产品不能调用 Jev。

## 2. Jev 的实际能力与不能假设的能力

已确认项目是 TypeSafe 的 Jev。它接受 `state + typed questions`，输出 `Choice`、`Score`、`Noul` 判断，不输出自由文案。同次请求中的问题各自独立、并行读取同一 state。[官方介绍](https://docs.typesafe.ai/introduction)

对本设计最重要的三个推论：

1. **一次选一个 pair，而非独立选两张卡。** 程序先生成合法的有序二元组，再让 Choice 选 `pair_id`。不能在一个请求里分别问“左边选谁”“右边选谁”，就假设第二问知道第一问的选择。
2. **循环状态由我们保存。** 下一轮把已选 pair、剩余配额和新反馈作为输入；官方接口没有可依赖的服务端 RNN 隐状态续接合同。这里的“像 RNN”是应用层的递归决策，不是宣称 Jev 的模型结构或在线训练机制。[State](https://docs.typesafe.ai/concepts/state)
3. **受控输出不等于正确推荐。** Choice 概率是当前选项集合内的模型判断，不能称为用户点击率；Noul 没有额外的 confidence 字段。官方也提示 CJK 表现弱于英语，必须单独评估中文内容。[Choice](https://docs.typesafe.ai/primitives/choice)、[Confidence](https://docs.typesafe.ai/confidence)、[Models](https://docs.typesafe.ai/models)

Jev 可以帮我们实现这种编排，但尚无本项目实验支持它一定比现有推荐更好。初版也不依赖跨请求 prompt cache、服务端记忆或自动个性化训练。

## 3. 统一数据流与模块接口

```text
内容来源 → 确定性准入 → 模型预过滤（既有或 Jev）→ 现有评估/分类 → 候选池
                                                              │
真实曝光与反馈 → 会话状态投影 → Director                        │
                                │                             │
                ┌───────────────┼─────────────────┐           │
                │               │                 │           │
         Jev 逐对编排     Agent 多屏草稿     Agent 多屏草稿     │
                │          + Jev 局部修正           │           │
                └───────────────┼─────────────────┘           │
                                ▼                             │
                   屏草稿 + 有序 pair 引用 ← 候选提案/确定性校验
                                │
                  最新约束复核 + 整屏原子提交
                                │
                       Batch → 实际曝光 → 反馈
```

这里的模块设计沿用 `codebase-design` 的原则：调用者只需要一个稳定的 Interface，复杂度留在模块 Implementation 内部；模型替换发生在清晰的 Seam，不把 SDK、重试和概率规则散落在推荐流程中。

### 3.1 Director 的外部 Interface

建议保留一个统一入口，概念签名如下，不是已存在的 Python 代码：

```text
Director.prepare_next(CompositionRequest) -> ScreenProposal
```

请求包含 FeedSession、StrategyStream、batch_intent、目标卡数、当前状态/约束版本、候选快照和 deadline；返回有序卡片及 pair 分组、来源归因、验证信息或明确回退结果。

Director 不拥有推荐 rows/shown 的独立写入路径。现有执行协调器接收提案，经最终校验后执行唯一的原子提交。内部预取和模型调用不能推进真实曝光、slot 或用户画像。

### 3.2 Jev 独立 provider

建议新增 `DecisionProvider` Interface，Jev 是其 Adapter；不伪装成 `LLMProvider.complete()`，不让它生成聊天消息。当前先实现真实需要的 Jev adapter 和离线 fake，不为尚不存在的供应商建立庞大框架。

```text
DecisionProvider.evaluate(DecisionRequest) -> DecisionEvidence

DecisionRequest:
  purpose: FILTER | CHOOSE_PAIR | AUDIT_DRAFT | REPAIR_PAIR
  state: 最小且带版本的上下文
  questions: typed questions + 受控 options/rubric
  request_id, model, question_schema_version, input_digest, deadline

DecisionEvidence:
  status: OK | UNAVAILABLE | INVALID
  actual_model, answers, usage, latency, input_digest
  answers: 按原语保留值/分布/可用的 confidence，不合成不存在的字段
```

`ABSTAIN` 是消费方对合法答案的决策，不与网络 `UNAVAILABLE` 混淆。模型返回未知 option、漏题、非有限数、非法分布等情况是 `INVALID`。Provider 可以记录技术调用日志，但无权删候选、改 Soul、重排数据库或提交推荐。

三个内部消费方各自拥有独立题目版本、阈值和回退策略：

- `FilterEvaluator`：把证据转成保留、候补或本轮排除的建议。
- `PairComposer`：提出合法 pair → 调用决策 → 校验 → 更新草稿。
- `DraftRepairer`：检查现有草稿 → 提出局部补丁 → 重验依赖后缀。

Agent 使用独立 `HorizonPlanner` Interface，输出结构化多屏提案；不强迫生成式规划与 Jev 原语共用一种返回类型。网络、模型版本、限流、总 deadline 在 Jev Adapter 内统一处理；业务权限与阈值留在消费方。

## 4. 最小领域对象与两种状态

| 对象 | 含义与不变量 |
| --- | --- |
| `SessionView` | 来自真实历史、曝光、反馈与明确会话意图的权威视图；含版本与 feedback cursor |
| `HorizonPlan` | Agent 的未来若干屏意图与草稿集合；纯 Jev 模式可为空 |
| `ScreenIntent` | 本次屏的目的、注册 recipe/lane、硬约束及软节奏；纯 Jev 可以从确定性规则获得 |
| `CardRef` | 有限候选快照内的 opaque ref，程序解析为 canonical item_key；不是模型任意生成的视频 ID |
| `PairDraft` | 两个有序 CardRef、位置、pair 意图、选择依据；不代表已曝光 |
| `ScreenDraft` | 当前屏暂定的有序 pairs、候选/上下文版本、revision；未提交，可废弃 |
| `DraftPatch` | 对指定未提交草稿 revision 的局部修改提案；包含原值摘要、替换项与后缀失效范围 |
| `BatchInstance` | 已被执行器原子提交的实际输出；提交后不可原位重写 |

`PairDraft` 是逻辑顺序，不强迫所有客户端改成两列布局。窄屏可以依序显示两张卡，后端“屏”仍对应请求/批次，不是物理视口大小。第一版不做 UI 重设计。

必须分清：

- **真实状态 `u_t`**：用户实际看了什么、明确表达什么；只有真实事件能推进。
- **草稿状态 `d_{t,k}`**：本屏暂定选了前 k 对、剩余什么；只能影响后续构造，不能写成“用户喜欢/看过”。

Agent 提前排出的第二、第三屏既不锁库存，也不写 shown。草稿内与跨未来草稿的暂定去重由程序维护；外部消费使候选失效时，未来草稿必须重新验证。

规划引用与执行引用分开：Agent 读取 PLANNING 快照内的 CardRef；执行时由程序解析 canonical item_key，再绑定最新合法 SERVING 快照。缺失、内容摘要/分类变更或 TTL 过期不能只换个 ref ID 就沿用旧判断，必须重验或替换。证据始终绑定当时的精确候选与特征版本。

初版整屏原子提交：提交之前都是 tentative；提交之后即使还没有 PRESENTED ack，也不能原位替换，因为响应可能已到达客户端。客户端新的曝光/行为再更新 `u_t`。

## 5. 模式一：Jev 每次选两张，逐对拼成一屏

### 5.1 一次 pair 决策看什么

输入不是整个候选池或整份对话历史，而是：

- 会话明确意图、少量可信偏好摘要，以及可归因的近期实际反馈；
- 当前屏目的、已选有序 prefix、前一屏实际曝光的简要摘要；
- 剩余名额/配额，已排除与已选引用；
- 一小组合法 pair，各卡包含必要标题、简短内容摘要、topic/style、来源和时效信息。

候选中的文字是资料，不能被当作指令。模型判断内容互补与当下适配，程序负责数数、去重和硬约束。

### 5.2 有限备选而非所有组合

现有 Curator 提供基础质量/相关性信号；Pair Proposer 从当前合法候选中构造互补、延伸、轻探索等备选，而不是只取单条分数最高的前两条。

初始实验可以从 20–40 张卡的 shortlist 产生 8–16 个有序 pair，含确定性基线 pair 和不同主题/风格的可行替代；这些是工程起点，不是证实最优值。不得枚举整个池子的 O(N²) 组合，也不得因为 shortlist 全是头部候选而让探索永远无法入选。备选必须满足当前已注册的配额与 guardrail，且为本屏剩余位置保留可行供给。

Choice 官方最多允许 255 个选项，但这里不追求用满。加入 `none_suitable`，避免所有备选不合适时被迫自信选一个。[Choice](https://docs.typesafe.ai/primitives/choice)

### 5.3 递归过程

```text
冻结本次 SessionView / CandidateSetRef / ScreenIntent
d0 = 空屏草稿

第 1 轮：合法 pair 集合 A0 → Jev 选 (A, B) → d1 = [A, B]
第 2 轮：基于 d1 重新提案 → Jev 选 (C, D) → d2 = [A, B, C, D]
第 3 轮：基于 d2 重新提案 → Jev 选 (E, F) → d3 = [A, B, C, D, E, F]

最新版本与完整屏约束复核 → 整屏 COMMITTED → 客户端曝光
实际反馈到来 → 更新 SessionView → 开始下一屏
```

六张卡只是例子；若请求十张，需五轮。目标为奇数时尾部允许一次单卡选择，绝不为凑 pair 重复或填入不合格内容；供给不足按现有 shortage 规则返回不足并留痕。

每轮的题目要窄，例如“在满足已给定意图的备选中，哪一对最适合接在已选内容后面”。如需评估独立维度，可以在同次请求中为**已知备选**逐一询问相关性、语义重复等，再由代码按版本化规则使用；不能询问“刚刚选中的 pair 是否合适”并假定它能读取同次 Choice 的答案。

本模式没有 Agent 多屏计划，不伪造 HorizonPlan/PlanSlot。可以后台预取一个下一屏草稿，反馈变化后使其失效或重新验证；预取不提交、不曝光、不推进计划。

### 5.4 这里“实时”的准确含义

同一屏连续选 pair 时，后一步能看到前一步**选了什么**，不能看到用户对尚未展示内容的反应。用户看完/操作当前屏后，新反馈可以改变下一屏及更远草稿。

如果以后要求“先展示两张，收到反馈，再即时补下一对”，需要另建 pair-level commit、流式追加和曝光协议。它是不同产品行为，不由本文的整屏原子提交偷偷实现。

## 6. 模式二：Agent 预编排，Jev 做局部修正

### 6.1 慢路径必须真的排到内容

仅让 Agent 输出“科技 60%、娱乐 40%”不足以实现用户说的“有设计的内容”。建议 Agent 一次给出未来三屏的：

1. 各屏的主题目标、节奏以及承上启下的意图；
2. 各 pair 的作用与暂定卡片引用；
3. 必须保持的语义约束、允许局部调整的范围；
4. 候选失效时可用的备选引用或替代意图。

这明确修订旧规格“Agent 绝不看具体候选”的限制：Agent 可以看**有上限的 shortlist、短摘要和 opaque refs**，但不读全池、不拿任意外部 URL 填计划、不把完整正文堆入上下文。数量上限和 token 预算单独配置；不够排满未来屏时允许尾部保持未解析意图，不能伪造候选。

`ScreenIntent` 与 `PairDraft` 分开保存：前者表达“为什么这样排”，后者表达“暂定是哪几张”。“教程接案例”等描述可以是解释性软意图；若现有 metadata 没有经过验证的 `content_form` 分类，不得把它宣称为可严格执行的硬配额。新增作者/平台 cap 也必须先进入注册约束，不能假装现有 selector 已支持。

三屏是滚动规划窗口，不是提前提交三屏。现有语义 slot 的推进规则仍由 batch_intent 管理：reshuffle 在同一意图上换一份草稿，不自动进入下一个 slot。

### 6.2 快路径先保留，再修补

每次需要下一屏时：

1. 程序先排除硬失效卡：已看、失效、明确屏蔽、scope 不符等，无需让 Jev 判断是否遵守硬规则。
2. 对仍可执行的草稿，Jev 基于最新 `SessionView` 检查：这个位置的内容是否仍适配当前意图？与前面是否语义重复？是否值得局部替换？
3. 检查可以批量并行，但每题明确指定 pair 与其假设 prefix；不能只依靠题目 ID 指向卡片，因为 Jev 不读取 question ID。[官方 Choice 请求结构](https://docs.typesafe.ai/primitives/choice)
4. 由程序根据校准阈值和修改预算定位问题位置，按最早受影响位置开始修补。
5. 替换备选允许 `KEEP / REPLACE_LEFT / REPLACE_RIGHT / REPLACE_PAIR`。这些是应用动作：Jev 选择程序提供的合法完整 pair，不直接执行数据库 edit。

例如 `(C,D)` 中仅 D 不合适，候选可以是 `(C,D)`、`(C,G)`、`(C,H)`；这样仍是一次 pair 决策，但只换了一张卡。如果 C 已被硬规则排除，所有包含 C 的选项包括 KEEP 都不可出现。

### 6.3 可以改中间，但不能忘掉后面

假设未来第二屏是 `[A,B] [C,D] [E,F]`，Jev 将中间一对改成 `[C,G]`：

- 第一对可保留；第三对的原有“适配性已验证”标记必须失效。
- 基于新 prefix `[A,B,C,G]`，重新验证第三对的重复、配额、衔接和供给条件。
- E/F 若仍合适，可保持原卡；失效的是旧证据，不是强迫所有后续卡重选。
- 若 G 原在未来第三屏，第三屏也要消除重复并复核；不能把未来草稿当作独立无关列表。

所有影响当前屏的验证必须在本屏提交前完成。更远未提交屏可先标记 STALE、后台修复，但提交前必须重新通过全量验证。

补丁采用 compare-and-swap：

```text
DraftPatch
  base_plan_revision? + base_draft_revision
  session_state_version + candidate_ref + constraints_digest
  target_screen + explicit_pair_positions + old_pairs_digest
  replacements: [{position, old_pair_ref, new_pair_ref, changed_card_refs}]
  invalidate_from_pair + affected_future_drafts
  evidence_ref + reason_codes + provider/model/schema versions
```

补丁只作用于仍未提交、revision 相符的草稿；服务器生成新 revision 并保留原计划与补丁链。不能只发送“删掉第 4 张”这种可能作用于错误版本的命令。

### 6.4 不让快路径逐渐篡改整个计划

局部编辑必须有明确预算与滞回规则。例如实验起点可设每屏最多替换 40% 的 pairs、按至少一对向下取整；同一 feedback cursor 不因同一份证据反复改同一位置。阈值需用中文样本标定，不在这里虚构通用 `0.8`。

下列情况应使 Agent 重新规划：明确会话目标转变；多个屏的主题意图失效；预计修复超出预算；池子缺货导致原路径不可行。预算包含因前序修改引发的实际后缀换卡，避免间接绕过上限。只重验但未换卡不消耗编辑预算。

硬规则优先于编辑预算。若保留原草稿会违反明确屏蔽，即使预算耗尽也不得输出它：当前屏使用最新约束下的 Jev 独立编排或确定性回退，标记策略替代；Agent 异步重规划剩余未提交窗口。旧计划已实际提交的前缀保持不变。

## 7. 模式三：纯 Agent 预编排

与混合模式共用同一份 `HorizonPlan / ScreenIntent / ScreenDraft` 合同，区别是编排时没有 Jev 检查或选择：

- Agent 一次排若干屏，程序校验引用存在、数量合法、去重、scope 与 guardrail。
- 需要当前屏时，程序复核最新约束，优先用仍合法的原草稿或已批准备选。
- 明确负反馈先通过现有确定性状态投影生效，不等待下一次模型响应。
- 草稿不够用或方向失效，异步请求 Agent；请求期限内无法完成时回落确定性推荐，不能暗中调用 Jev。

正常路径是真正执行 Agent 的具体内容安排，不是把 Agent 退化成只出配额；故障路径允许 baseline，但必须记录 `requested_strategy != realized_strategy`，不能把 baseline 效果记到 Agent 的成功样本里。

## 8. 过滤：共享 Jev provider，不共享业务策略

### 8.1 建议先接 discovery 的相关性过滤

```text
抓取 → 确定性硬规则 → 模型预过滤（既有 embedding 或 Jev）
     → 当前 LLM 评估/分类 → 入池
```

对有限候选分别询问“是否与明确兴趣有关”“是否属于不希望看到的语义内容”等窄问题；使用 Noul/Score 是接口选择，最终保留/排除策略由代码拥有。每题显式定位 candidate；同一批处理的样本共享有限上下文，不发送整个用户历史。

初期 shadow；enforce 也只在经过领域校准且证据足够时做本轮软排除。临界结果、模型故障、信息不足保留给当前流程，探索候选有独立的保留策略。学习兴趣的门槛不能锁死所有新方向。

现有 `discovery.eval_prefilter_mode` 不会被新 provider 隐式删除：Jev 关闭时完全保持既有行为；初版同一候选准入路径不允许 embedding prefilter 与 Jev filter 同时 enforce，另一个可 shadow。配置验证必须明确这种互斥，不能悄悄串联两道模型拒绝。

校准 cohort 在任一模型过滤器排除之前冻结。需要人工/LLM 对照标签的样本绕过两个模型的拒绝，但仍遵守确定性硬过滤。当前 learned/shadow 校准已有将 embedding enforce 降为 shadow 的逻辑；接入 Jev 必须延续这种不截断对照样本的纪律，不能让 Jev 把难例提前滤掉，制造虚假的高准确率。

### 8.2 不能混淆长期准入与此刻不想看

“这个内容与我的稳定兴趣无关”可用于发现阶段的版本化准入判断；“今天别给我论文”只进入短期会话过滤或屏内选择，不能永久删除候选，更不能写成长期不喜欢这个领域。

若以后开放 pre-serve Jev filter，它与 discovery filter 分别配置、分别留痕。任何过滤变化都更新候选/约束摘要，使依赖旧集合的草稿重新验收。

### 8.3 现有实现限制

当前 `discovery/learned_scorer.py` 是 embedding/BM25 组合，并不是 Jev；现有 discovery 仍依赖 LLM 补充 temporal/topic/style/franchise 等 metadata。Jev 返回一个相关性概率不能自动替代这些工作。

因此第一阶段 Jev 是新增可选语义过滤，不宣称“一接入就移除所有 LLM 评估成本”。需测量省下的后续评估调用是否抵得上额外调用、误杀和维护开销。过滤的 false negative 比较必须覆盖被拒候选，不能只看最终留下的样本。

## 9. 一个完整例子：预排三屏，收到反馈后只换中间

下面均为示意候选标题，不是真实推荐记录；假定一屏六张、三对。单屏 card 数是例子，不写死在产品中。

用户最近在看“本地 AI 应用”，Agent 先排：

| 屏 | 第 1 对 | 第 2 对 | 第 3 对 | 意图 |
| --- | --- | --- | --- | --- |
| 1 | `[A 本地模型体验, B 小工具演示]` | `[C 用法概览, D 实际案例]` | `[E 相关新方向, F 轻量体验]` | 建立当下可用的内容入口 |
| 2 | `[G 配置经验, H 小项目]` | `[I 深入原理解读, J 相关长讲]` | `[K 效果对照, L 实践复盘]` | 原本准备从体验转向深入 |
| 3 | `[M 应用延伸, N 跨领域案例]` | `[O 相邻方向, P 入门体验]` | `[Q 回访内容, R 轻探索]` | 拓宽但不突然跳离兴趣 |

第一屏已提交并曝光。用户明确说：“今天想看能马上用的，先别给我原理长讲。”

系统先更新短期会话约束，再做第二屏的修正：

1. `[G,H]` 仍适配，保留。
2. `[I,J]` 不合适，程序提供合法备选；Jev 选 `[S 配置排错, T 可运行案例]`。
3. 以新 prefix 检查 `[K,L]`；仍合适则原样保留。
4. 第三屏按新意图标记复核；若整体目标已不合适，交给 Agent 重排，而不是 Jev 零碎改完整个未来窗口。
5. 第二屏最终以 `[G,H] [S,T] [K,L]` 原子提交，第一屏绝不原位换卡。

同一场景下：

- **纯 Jev** 没有上述三屏草稿，直接按新会话状态，依次选择 `[G,H]`、`[S,T]`、`[K,L]`。
- **混合** 保留 Agent 设计，只修第二屏的中间一对。
- **纯 Agent** 立即让程序排除被明确否定的内容；Agent 重排未来草稿，来不及则用合法备选/规则回退。

“配置经验/原理解读”等示意需要内容摘要支持；如果当前 metadata 无法可靠识别，则不能悄悄用标题关键词当绝对真值。明确用户限制要转成可执行字段或保守排除，并记录无法确定的情形。

## 10. 延迟、预算与失败行为

### 10.1 串行成本必须正视

一屏十张需要五轮依赖式 pair 决策，不能简单并行。官方发布文章报告 70–500ms 单次延迟且说明了测试环境；按该数粗乘五次也已是 0.35–2.5 秒，还未计入本地网络、候选构造、校验和重试，不能作为我们的上线 SLA。[官方发布文章](https://typesafe.ai/blog/introducing-system-one-models-and-jev)

建议优化顺序：

1. 提前计算下一屏草稿，反馈到来后局部使其失效；永不把预取当曝光。
2. 混合模式对原草稿批量审查，保持路径尽量少调用；修补才串行选 pair。
3. state 只保留必要真实历史与当前 prefix 的压缩摘要；卡片摘要复用，引用/顺序不能丢。
4. Adapter 显式遵守一次决策总 deadline 与整屏总 deadline；退避重试不能无限拉长请求。

不要假定 provider 跨请求 cache。应用缓存键至少绑定 actual model、题目/schema、精确输入摘要、候选/约束/会话版本和 prefix digest；结果复用仍须最新硬约束复核。Jev 原始输入 token 与失败重试都纳入成本。

Agent 规划预算与 Jev 编排预算分开。原有“每会话最多若干 Agent 规划调用”不能拿来限制每屏多次 Jev 决策；Jev 需每屏最大调用数、最大修复数、全局并发/限流和 circuit breaker。具体毫秒与阈值待中文样本及实际网络基准后锁定。

### 10.2 回退矩阵

| 失败位置 | 行为 |
| --- | --- |
| Jev filter 超时/非法/不确定 | 保留候选进入现有评估；不执行由故障产生的拒绝 |
| Jev pair 选择超时/拒选/证据不足 | 在最新硬约束下确定性选择；其余位置可整体切 baseline，记录降级 |
| 混合模式修补失败 | 原草稿仍合法才保留；否则确定性修补或整屏 baseline |
| Agent 没有可用计划 | hybrid 可 Jev 临时编排；agent_only 只能确定性回退，同时异步补计划 |
| 提交前上下文/候选版本过期 | 废弃旧证据，有限次 rebase；不得提交过时硬约束下的内容 |
| 持续反馈导致构造反复失效 | 有界重试后按最新约束 baseline，避免模型调用无限循环 |
| 约束下没有足够内容 | 明确不足或空结果；不放宽用户禁令来凑屏 |

所有回退仍经过统一 FinalPolicyVerifier 与提交审计。不能为了保体验绕过安全检查；baseline 也无法合法提交时返回明确不足/重试结果。

## 11. 版本、一致性与归因

### 11.1 一次草稿尝试绑定一份状态

最小绑定集合：

```text
feed_session / strategy_stream / generation_request
requested_strategy / configuration_revision / runtime_epoch
session_state_version / feedback_cursor / constraint_version
candidate_set_ref / feature_schema_version / shortlist_digest
plan_revision? / slot_id? / screen_draft_revision / pair_index / prefix_digest
model / question_schema / decision_policy_version / experiment_assignment
```

模型调用期间若产生新状态，不能一边沿用旧 prefix、一边悄悄解释为已经按新意图选择。反馈投影可将尝试标为 STALE；重取版本并从最早受影响位置 rebase。最终提交必须核对最新硬约束及有效版本，失败按有界重试与回退处理。

原有 request 幂等、slot lease、fencing、runtime epoch、唯一原子提交继续生效。纯 Jev 无 slot 时使用 stream/request 级 fence，不通过伪造 Agent slot 获取提交权。

切换编排模式或 provider/model 配置会 bump configuration revision/runtime epoch，使未提交旧模式草稿及在飞结果失效；已提交批次仍可恢复和归因，不重新生成。不能让晚到的旧 Jev 结果覆盖刚切换后的纯 Agent 草稿。

### 11.2 不让不同模式的成本与效果混在一起

日志至少分开：

- 用户/实验指定的模式 `requested_strategy`；
- 实际执行的模式与各 pair 的来源 `realized_strategy / pair_origins`；
- 原始 Agent 草稿、Jev 检查结果、真正生效的补丁、最后提交序列；
- filter route/model/规则版本与通过/排除原因；
- 真实曝光与反馈，provider 延迟、调用次数、token、失败和回退。

`agent_only`、`agent_jev` 的计划引用非空时才统计计划兑现率；`jev_recurrent` 没有 Agent 计划，不能用“无计划 fallback”惩罚它，也不能伪造计划采用率。原 `PLAN_SLOT_SUBSTITUTED` 只能用于确实有合法 plan slot 被替代的场景，不能承接所有 Jev 路径。

Choice 概率不是实验 assignment propensity，也不是直接可用于离线策略评估的行为概率。模型默认返回最优项；若未来引入随机采样，必须另记实际采样策略与归一化概率。

### 11.3 无 Agent 计划时，执行归属也是正式分支

模式与执行归属是不同的字段。不得继续沿用基础规格中“plan 为空就是 baseline”的简写：

| 路径 | 正常推进的 execution binding | 同方向换批 |
| --- | --- | --- |
| Agent / hybrid 有有效计划 | `PLAN_SLOT` | `STAY_PATCH` |
| Jev 独立编排 | 新 `SESSION_STEP` | 新 `SESSION_STAY` |
| Hybrid 尚无有效计划、临时用 Jev | `SESSION_STEP`，记录 bootstrap/degradation 原因 | `SESSION_STAY`，不得冒充执行 Agent slot |

`SESSION_STEP / SESSION_STAY` 中 `plan_id / revision / digest / slot_seq`、slot lease/fencing 及 origin-plan/slot 字段必须成组为空；FeedSession、generation claim、screen direction、candidate/constraint refs 和 stream/request fence 则必须完整存在。`SESSION_STAY` 的 origin root batch/patch sequence 不为空，但不绑定虚构的 origin slot。

增加独立的 `SessionStepPolicy` 判别类型，冻结本屏 `direction_ref/digest`、注册约束和探索预算；不能套用强制引用 Plan 的旧 CompiledPolicy。`SESSION_STAY` 沿 root batch 的 direction 合同、结合**最新**会话状态重选，排除 root 及已提交替换链的内容。最新硬限制冲突时不得为保方向而违反限制，转入显式方向失效/重新推进流程。

替换链计数与链尾 CAS 必须能存于 root batch chain，而不只存于 PlanSlot。区分 `committed_batch_seq`（每次成功提交批次）、`experience_step_seq`（成功提交正常推进的方向合同）与 `state_revision`；STAY 不推进 experience step，RESTORE 不运行任何选择器也不推进这些计数。COMMITTED 只更新提交/排重账本，不冒充 PRESENTED 或正反馈。

新增 session 执行的闭合 outcome 映射建议如下；正式 schema 需按此扩展旧 XOR 校验：

| requested → committed | outcome / 原 committed_mode | 归属 |
| --- | --- | --- |
| `SESSION_STEP → SESSION_STEP` | `SESSION_STEP_COMMITTED / enforce` | SessionStepPolicy；无 Plan；推进 experience step |
| `SESSION_STAY → SESSION_STAY` | `SESSION_STAY_COMMITTED / enforce` | SessionStepPolicy；root/替换链完整；不推进 experience step |
| `SESSION_STEP / SESSION_STAY → NORMAL_BASELINE` | `BASELINE_COMMITTED / baseline` | 原 session 执行租约关闭；不消费 slot、不推进 experience step；保留失败 provenance |

Jev 单个 pair 失败后，确定性 composer **仍完成有效方向合同**，是正常 session/plan commit 加 `PAIR_CONTROLLER_FALLBACK`，不是整屏 baseline。只有放弃方向合同、整体使用 baseline 时才使用 baseline outcome。Agent 有效 leased slot 被整体 baseline 替代时，仍用旧 `PLAN_SLOT_SUBSTITUTED / BASELINE_SUBSTITUTED` 并同事务标记 slot；slot 已撤销则回到无 slot baseline。

曝光与反馈按 committed execution binding 分派：session 分支不能生成虚假的 SLOT_COMMITTED/PRESENTED 或计划兑现率。下一版生成合同需支持通用 advance/append；旧 `ADVANCE_PLAN / APPEND_PLAN` 在版本化适配层映射，不强迫 Jev 用户创建 Agent Plan。

## 12. 配置方向与仓库落点

以下是配置维度示意，不是已支持的 TOML：

```toml
[director]
mode = "off"                         # rollout: off | shadow | enforce
strategy = "agent_jev"                # jev_recurrent | agent_jev | agent_only
composition_step_size = 2
planning_horizon_screens = 3          # Agent 模式使用；不是提交三屏
commit_horizon_screens = 1
decision_provider = "jev"

[decision.providers.jev]
model = "jev-1.13.0"                  # 实验固定版本；接入时重新确认可用版本
api_key_env = "TYPESAFE_API_KEY"       # 仅变量名，不在文档/日志保存实际值

[discovery.semantic_filter]
mode = "off"                         # 独立灰度，初次试验用 shadow
provider = "jev"

[recommendation.session_filter]
mode = "off"                         # 后续按需开放；不是编排模式的隐含行为
provider = "jev"
```

同一个 Jev provider 配置可被不同模块引用；filter 和 compose 的 rubric/阈值不同。启用某条 Jev 路由才校验其凭据，不能让未启用 Jev 的纯 Agent/规则模式被缺失 key 阻塞。运行时故障走回退；启动配置错误要明确报错，不能静默把用户配置当成功启用。

实现位置建议：

- 新 `decision/` 模块集中 provider Interface、typed request/evidence、Jev Adapter 和 fake；借鉴 `llm/registry.py` 的独立 embedding registry 形态，不改造为聊天 completion。
- `recommendation/director/` 集中模式选择、HorizonPlanner、PairComposer、DraftRepairer 与相关 store；`curator.py` 的现有基础信号继续复用。
- `discovery/engine.py` 的既有 prefilter/evaluation 接线消费 FilterEvaluator；不把 Jev 特例散落到每个 source。
- batch/presentation/feedback 身份桥先完成，避免在混合历史列表上假装拥有完整“屏”语义。

本轮仅改设计文档与研究笔记。运行时代码、依赖、CLI、安装器和现行 config 未变，因此不把未实现方案写进已实现架构图或模块功能表。实现时按 CLAUDE.md 的 Documentation Requirements 同步所有受影响文档。

## 13. 分阶段实施与验收

1. **共同底座**：完成基础规格 Phase 0A 的 batch 身份、曝光确认、feedback 归因与确定性回退；补齐本文多模式的版本化合同与 fixture。
2. **独立 Jev provider**：typed fixture/fake、输入上限、错误/重试/deadline、模型日志、隐私最小化；不需要先上导演就能单测。
3. **中文 filter shadow**：抽取真实分布样本与被拒样本，人工标注；分开测兴趣相关、短期适配、探索误杀。未通过验收不开 enforce。
4. **纯 Jev pair composer shadow**：验证递归、备选覆盖、整屏重复/多样性、费用与 p95；检查 proposer 是否已决定了全部结果。
5. **Agent 多屏草稿 + agent_only**：有限候选引用、真实具体编排、草稿失效与非阻塞补计划，先建立慢路径对照。
6. **混合局部修正**：加入批量审查、替换单卡/单对、中间修改后的后缀复核、预算升级、patch CAS。
7. **受控实验**：同样 filter、候选供给、曝光协议下比较 baseline / Jev / Agent / hybrid；单独评估 filter，避免混杂。个人低流量先用盲评与回放发现问题，不宣称已有统计显著提升。

必须覆盖的测试：

- 同请求问题独立：第二对输入确实包含第一对结果，而不是错误地发三个并行 Choice；pair 两卡不能相同。
- 草稿 prefix 与真实曝光隔离；预取、模型检查、重规划不能写 shown/喜好。
- 三模式×filter on/off 的调用计数；agent_only 编排绝不调用 Jev，Jev-only 不意外调用 Agent。
- 只换一张、替换中间一对、触发后缀重复、future draft 冲突、预算超限和 hard block 例外。
- 已 COMMITTED 但尚未 PRESENTED 的 batch 不可修改；迟到 patch/CAS 冲突没有部分写入。
- provider 429/529/超时、非法分布、未知 option、none_suitable、中文指令注入、deadline 耗尽与模型版本漂移。
- 奇数卡数、候选不足、reshuffle 不推进 slot、无 Plan 的 Jev 流程、模式切换 fence、恢复/幂等不重复曝光。
- 日志区分原始意图、实际执行和失败；所有随机分配会话纳入 ITT，不只统计完成规划者。

质量验收不只看单条 CTR：屏级满意度、负反馈、重复感、内容节奏、继续浏览与退出都需看。单次跳过不等于讨厌，停留更久也不天然代表更满意。离线 pair 偏好只能作为代理指标，不能当作上线效果。

## 14. 与基础规格的替代关系

本文是新需求的权威架构增补，不能同时将旧限制视为三模式的硬规则：

| 基础规格旧条款 | 本文替代/保留 |
| --- | --- |
| §1、§3、§5、§11.2、§29：Agent 不看候选、不选具体内容 | Agent 可看有限 shortlist 与短摘要、选择快照内 CardRef；禁止全池 prompt 与虚构引用仍保留 |
| §7–8、§15：只有低频规划 + 确定性选片 | 三种编排策略共享确定性提案/校验/提交；Jev 可参与高频 pair 选择 |
| §10–12、§17–18：必经 Agent Plan/PlanSlot 与原 execution-kind 联合 | Agent 模式保留；Jev 使用 SESSION_STEP/SESSION_STAY、无 Plan 的 SessionStepPolicy 与 stream fence；STAY 链不依赖虚构 slot |
| §11.1：PREFETCH 仅生成能力快照 | 可进一步准备模型草稿/审查证据，但仍不提交推荐、不写 shown、不推进实际体验；草稿写入不是业务曝光 |
| §14：PATCH 只修改下一个 PENDING slot 的受限 lane | 保留确定性反馈投影与总体 Gate；新增独立 DraftPatch，可修未来任意未提交草稿的局部 pair，并使依赖后缀失效 |
| §18、§22：旧 planner 调用/并发预算 | Agent 预算仍独立生效，Jev 有屏级调用/deadline预算；共同服从 runtime kill switch |
| §21：只有 rollout mode | rollout、strategy、provider route、filter route 分开；上文配置只是设计草案 |
| §24、§27：单模式实施顺序 | batch/feedback 前置底座不变，后续按本文三模式分阶段推进 |
| §12、§18–20、§23：原子提交、安全、审计、反馈归因 | 原则保留；COMMITTED 不可原位改写、候选精确绑定、最终硬规则校验、shadow 隔离、ITT 仍强制 |

旧文 JSON 样例没有自动升级为本文的运行时合同。动手编码前需正式发布新的 schema 版本和对应验收 fixture，尤其明确无 Plan 分支、DraftPatch、pair attribution、模式切换与失效规则。

最终产品理解应当是：**Agent 可以提前写好节目单；Jev 可以独立逐对编排，也可以只改节目单里不再合适的几处；程序始终掌握真实状态与最终播放权。**
