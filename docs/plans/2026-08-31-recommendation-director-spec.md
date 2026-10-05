# Recommendation Director（推荐导演）完整规格

**Created:** 2026-08-31  
**Revised:** 2026-09-26
**Status:** Agent-first foundation; extended by the three-mode design below; implementation not started
**Scope:** OpenBiliClaw recommendation strategy layer  
**Research sources checked:** 2026-08-31
**Repository interfaces checked:** 2026-09-26
**Source context:** `docs/architecture.md`, `docs/modules/recommendation.md`,
`src/openbiliclaw/recommendation/engine.py`,
`src/openbiliclaw/recommendation/curator.py`,
`src/openbiliclaw/discovery/pool_snapshot.py`

> **2026-09-26 三模式增补：** 最新需求见
> [Jev 逐对编排、双速修正与纯 Agent 模式](2026-09-26-director-jev-modes.md)，
> Jev 接口事实见 [官方能力核实](2026-09-26-jev-provider-research.md)。
> 本文保留 batch/曝光/反馈、约束、提交、安全与审计底座；原有“Agent 不看候选、
> 不选择具体卡片、仅一个低频 Planner 模式”等限制不再代表最新产品方案。
> 增补文档 §14 列出了替代范围；本文 JSON 合同尚未升级到三模式，不能不经修订直接编码。

## 1. 一句话决策

Recommendation Director 是一个 **受控的、事件驱动的滚动时域规划器**：

- 它低频规划未来三个语义 slot 的主题 lane、角色和探索节奏；
- 它只租约并提交下一个 slot，用户反馈后可以保留、修补或重写仍为 `PENDING` 的 slot；
- 它不读取完整候选，不输出视频 ID，不在线逐条打分，也不自由修改排序权重；
- 现有 `PoolCurator + MMR + diversity guardrails` 继续高频选择具体内容；
- Director 超时、失效、缺货或输出非法时，系统无感回落现有推荐路径。

核心变化不是把当前推荐器换成 LLM，而是在它上方增加一个问题：

> 这一屏在用户接下来的内容体验中承担什么作用？

然后仍由现有引擎回答：

> 哪些具体内容最适合完成这个作用？

## 2. 为什么现在需要这一层

OpenBiliClaw 已有成熟的候选和单屏执行能力：

- candidate pool 持有经过发现、评估、分类和文案预计算的候选；
- `PoolDistributionSnapshot` 已能表达饱和主题、风格、IP、平台缺口和供给不足；
- `PoolCurator` 已处理相关性、发布时间价值、主题疲劳、来源单调性、惊喜度和显式反馈；
- 推荐热路径已有 MMR、topic/style/broad-topic 上限、amplification guard、平台 scope、跨平台补位和已看去重；当前 selector **没有 source/platform cap**，若以后做来源配额属于新增执行能力；
- recommendation rows 与 `pool_status='shown'` 已在独立事务中原子提交；当前还没有持久化的 BatchInstance；
- like、dislike、dismiss、reshuffle、对话和推断满意度已有反馈/Soul 入口；`/api/saved/*` 的保存动作尚未携带 recommendation/batch 归因进入 EventIngress，Phase 0 必须补事件桥后，save 才能成为 Gate 信号。

同时，当前仓库有两个 Director 上线前必须先修的事实：

- `GET /api/recommendations` 会先取历史未处理 rows，不足时再调用 `serve()` 补货，因此一个可见列表可能混合多个生成批次；它还是一个会写推荐和 `shown` 的读请求；
- `serve()` 提交不等于用户真的看见，虽然已有 `presented` 字段和 `mark_presented()`，推荐 API 尚未形成完整的客户端曝光确认闭环。

因此本规格把后端执行单元正式定义为 `BatchInstance`，并区分
`COMMITTED` 与 `PRESENTED`；“屏”只保留为产品隐喻。

当前缺口不是“再做一个更聪明的 item ranker”，而是：

1. 已有跨批 topic/source fatigue，但没有显式 multi-slot intent，无法表达“深入、桥接、试探、恢复”等批次角色和路径；
2. 反馈能影响单条候选与长期画像，但没有一个会话级状态负责修改尚未曝光的内容路径；
3. discovery 知道池子缺什么，recommendation 知道当前什么分高，但二者之间没有一份可审计的短期内容策略；
4. 推荐文案解释“为什么推这一条”，却不知道这一条在整段体验里为什么此时出现。

Director 补的是这一层短期策略，不复制已有召回、排序、画像或反馈系统。

## 3. 目标与非目标

### 3.1 v1 目标

1. 将推荐从独立批次提升为可观察、可修正的连续三-slot 体验。
2. 让 LLM 只在受控的 `topic_id / style_key / recipe_arm` 空间规划。
3. 把计划编译成现有推荐引擎可以确定性执行的配额、过滤和约束。
4. 将即时反馈先写入 session state，只有明确转折才触发低频重新规划。
5. 保持 Director Planner 不进入推荐请求热路径，且全链路可回退、可审计、可压测；现有可选 `expression_mode="realtime"` 仍可能在 serve 中调用 LLM，不在本规格中伪称消失。
6. 从第一天留下可做合同/状态 replay 和随机 A/B 的决策日志；只有真实记录 propensity 后才允许做 off-policy evaluation。精确 item reselection replay 只承诺 CandidateSetRef 尚未清理的短窗口或版本化 fixture，不无限期保存全部未选候选。

### 3.2 v1 非目标

- 不让通用 LLM 逐候选预测点击、观看或喜欢概率。
- 不让 LLM 选择、排序或虚构具体视频。
- 不让 LLM 输出任意连续浮点权重，覆盖已经标定的 `ScoringWeights` 或 MMR 参数。
- 不做端到端生成式推荐模型、PPO、SlateQ 或在线 RL。
- Director 自己不额外因单次点击、跳过或换一批写长期 Soul/Profile；现有 Soul pipeline 仍会把 recommendation click 作为强 INTEREST/SURFACE signal，本规格不篡改该语义。
- 不把“主题弧线一定改善满意度”当成已证实事实；它是需要 shadow 和实验验证的产品假设。
- 不在 v1 让 User Twin/critic 成为线上阻塞依赖。
- 不提前为未来三个 slot 锁定候选或把候选标记为 shown。
- 不在 v1 声称能可靠区分“论文解读 / 案例 / 实操”；当前 `style_key` 是观看方式。精确控制需新增正交的 `content_form` 分类后再开放。
- 不在 v1 启用自由文本新方向补货、来源配额、任意时长配额、`contrast` 或自动 `close`。

## 4. 证据与假设边界

### 4.1 已有较强证据支持的部分

| 设计 | 依据 | 本项目迁移方式 |
|------|------|----------------|
| LLM 规划高层兴趣簇、传统模型选择 item | [Google/DeepMind 2024](https://arxiv.org/abs/2405.16363) | Director 只输出受控 `topic_id` lane；Composer 在池内选 item |
| novelty proposal 与 feedback alignment 分离 | [Google/DeepMind 2025](https://arxiv.org/abs/2504.05522) | Planner 负责提出计划；validator/未来离线 critic 独立判断可执行性和对齐 |
| LLM 表达意图、专业推荐工具执行 | [Microsoft InteRecAgent](https://github.com/microsoft/RecAI/tree/main/InteRecAgent) | LLM 只看聚合与引用，候选留在 Candidate Bus；一次 plan-first，不逐 item ReAct |
| 单屏应作为 slate 整体优化并考虑退出价值 | [YouTube LRF](https://arxiv.org/html/2408.06512) | 指标覆盖整屏、继续浏览和离开，不只看单条点击 |
| 连续 slot 不能都做局部贪心 | [Kuaishou STCRank](https://arxiv.org/abs/2601.10027) | 三-slot 采用 look-ahead，但只提交下一个 slot；不照搬其模型架构 |
| 个性化解释能帮助用户理解长尾内容 | [Spotify Research 2024](https://research.atspotify.com/2024/12/contextualized-recommendations-through-personalized-narratives-using-llms) | 受控 `expression_intent_code` 进入文案层，不反向决定 item |
| 生产级 LLM 推荐仍需 catalog grounding、下游 ranking 和真实 A/B | [Spotify GLIDE, KDD 2026](https://research.atspotify.com/publications/from-habits-to-discovery-deploying-llms-personalized-generative-recommendations-spotify) | OpenBiliClaw 不训练 Semantic-ID generator；借鉴其 grounding、候选层接入和在线实验纪律 |
| 传统和 LLM ranker 应是可替换、有缓存的执行组件 | [gorse](https://github.com/gorse-io/gorse) | Director 不绑定某个 ranker；计划与编译结果有 TTL 和基线 fallback |
| Agent 记忆自编辑可作为长期研究方向 | [AgentCF](https://arxiv.org/abs/2310.09233) | v1 不让 Director 自改长期记忆；证据仍交给现有 Soul consolidation |
| LLM 更适合提供 bandit 先验，而非替代在线反馈学习 | [EMNLP 2024](https://aclanthology.org/2024.emnlp-main.1107/) | 只作为 Phase 5 研究依据；v1 不用合成偏好训练线上策略 |

### 4.2 仍需验证的产品假设

以下没有被上述工作直接证明：

1. 一次看三屏的滚动计划优于每屏独立策略；
2. `deepen → bridge → explore` 等内容弧线会改善视频/跨源内容流满意度；
3. 单用户实时反馈足以可靠判断何时转场；
4. 推荐理由说明“当前屏角色”比只说明单条相关性更有价值；
5. Director 的额外复杂度在个人、本地项目尺度上值得。

因此 v1 必须先 shadow，且 Director 不能拥有不可回退的独占控制权。Shadow 只能证明合同、可行性、延迟和护栏，**不能证明用户会更喜欢没有真正曝光的方案**；效果必须由真实随机实验验证。

## 5. 核心原则

### 5.1 三频分工

```text
低频（分钟 / 关键事件）
Director Planner
  规划三-slot recipe arm、主题 lane 与探索方向

中频（每个新批次）
Intent Projector + Feedback Gate + Policy Compiler
  归一化反馈、判断继续/修补/重规划、编译当前 slot 策略

高频（每次 serve）
PoolCurator + Composer
  读取候选、打分、配额选择、MMR、去重、护栏、原子 shown 提交
```

### 5.2 看三步，只承诺一步

- `planning_horizon = 3`：Planner 需要表达连续性。
- `commit_horizon = 1`：只有下一个 `PENDING` slot 可以获取 lease 并实例化具体候选。
- 第 2、3 个 slot 始终只是意图，不预占候选、不写 shown。
- slot 一旦 `COMMITTED` 就不可重写；只有仍为 `PENDING` 的未来 slot 可被 Gate 保留、修补或 supersede。
- `PRESENTED` 由客户端曝光确认产生，用于反馈归因；它不是数据库提交的同义词。

### 5.3 LLM 输出意图，程序执行约束

不可信的 `DirectorProposal` 输出：

- 命名且版本受控的 recipe arm；
- 受控主题 lane 的 `min / target / max` 配额；
- v1 允许的 style 偏好；
- 有界的 exploration 与表达意图 code。

DirectorProposal 不输出：

- 候选或推荐 ID；
- 标题、URL、UP 主名单；
- SQL、工具名或执行步骤；
- 任意 Python 表达式；
- 自由浮点 ranker 权重；
- 是否绕过 dislike、seen、平台隔离或安全规则。
- `plan_id`、revision、状态、时间、lease、hard filter 或候选快照成员。

`role` 由服务器根据 recipe registry 派生，避免模型同时声明语义角色和执行 recipe 后互相矛盾。v1 的 recipe 参数、relaxation ladder 和兼容版本全部由代码拥有。

### 5.4 长期画像与会话意图分离

- Soul/Profile 是慢变量，Director 只读其 compact snapshot 和 generation/digest。
- Session State 是快变量，保存本次会话的临时目标、接受/拒绝方向、内容密度和计划进度。
- 快变量可以改变下一屏，但不能直接晋升为长期画像。
- 长期变化继续由现有 cognition / consolidation 链路负责。

## 6. 术语

| 术语 | 定义 |
|------|------|
| Screen | 仅用于产品表达；后端权威对象是 `BatchInstance` |
| FeedSession | 客户端显式创建的推荐会话，绑定 profile、surface、source scope 和 feed mode |
| StrategyStream | 同一 `profile + feed_session + surface + source scope + feed_mode` 的连续策略流；规划 single-flight 与 cooldown 的作用域 |
| PlanLane | 某个 PlanSlot 内的 topic 配额单元；完整身份为 `(plan_id, revision, slot_seq, lane_key)`，裸 `lane_key` 只在该 slot 内唯一 |
| Candidate Bus | 现有候选数据库和读取接口；完整候选永不进入 Director prompt |
| CandidateSetRef | 对一次候选池能力快照的不可变引用和聚合摘要 |
| DirectorProposal | LLM 产生的不可信三-slot 提案，不含服务端 ID、状态或 hard policy |
| DirectorPlan | 服务器验证并盖章的不可变三-slot 计划 |
| PlanSlot | DirectorPlan 中一个语义步骤，状态独立存储，不含具体 item |
| CompiledPolicy | Policy Compiler 为一个 leased slot 和精确候选快照生成的确定性合同 |
| BatchInstance | 一次实际生成的推荐批次，具有 commit/present/close 生命周期 |
| Gate | 根据反馈、计划和候选池状态输出 `CONTINUE / PATCH / REPLAN / FALLBACK` 的规则组件 |
| Recipe | 经测试、版本化的策略预设；v1 不改变 Curator 基础权重 |
| Basis | 计划生成时绑定的 profile、session、taxonomy、recipe 和 pool capability 版本 |

## 7. 总体架构

```text
Client ── create/resume FeedSession ──────────────────────────────────────────┐
  │ explicit batch_intent + request_id                                        │
  ▼                                                                            │
FeedSessionManager ── scope / idempotency / slot lease                         │
  │                                                                            │
  ├──── EventIngress → IntentProjector → session overlay → Feedback Gate ─┐    │
  │                                                                        │    │
  └──── next PENDING slot ───────────────────────────────────────────────┐  │    │
                                                                         │  │    │
Soul/Profile ─┐                                                          │  │    │
Pool capability cube ─→ Context Builder → async Director Planner         │  │    │
Session summary ─┘                   │ untrusted DirectorProposal         │  │    │
                                     ▼                                   │  │    │
                    Schema/Semantic/Feasibility Validator                │  │    │
                                     │ server-stamped DirectorPlan       │  │    │
                                     ▼                                   │  │    │
                     Plan/Slot Store (CAS + lease + TTL) ◀───────────────┘  │    │
                                     │                                      │    │
Fresh SERVING CandidateSetRef ─→ Policy Compiler ─→ CompiledPolicy          │    │
                                     │                                      │    │
                                     ▼                                      │    │
PoolCurator → constrained Composer → MMR/existing guardrail policy           │    │
                                     │                                      │    │
                         FinalPolicyVerifier                                 │    │
                                     │ short fenced transaction              │    │
                                     ▼                                      │    │
recommendations + pool shown + BatchInstance COMMITTED + DecisionLog         │    │
                                     │                                      │    │
Client presentation ack ─────────────┴→ PRESENTED → feedback attribution ────┘    │
                                                                                  │
Baseline path is always available and never waits for the Planner ◀───────────────┘
```

### 7.1 唯一权力顺序

所有冲突都按下列顺序解决：

```text
法律 / 安全 / temporal eligibility / platform scope / seen 等系统过滤
  > 用户明确的 session 或 durable 限制
  > 现有 selector 的质量、多样性与 amplification guardrail policy
  > Director 的 recipe、lane 和 style 软目标
  > PoolCurator/MMR 在可行集合中的优化
```

Director 不能放松前三层。现有 selector 内部哪些 cap 可按既有算法退让、哪些不可退让，由版本化的 `guardrail_policy_version` 决定；Director 本身没有开关。

## 8. 组件职责

### 8.1 FeedSessionManager

负责创建并持久化推荐会话，scope 至少包含：

```text
profile_id, feed_session_id, surface, source_platform_scope,
feed_mode, default_batch_size, client_instance_id
```

它为每次生成请求要求 `request_id + batch_intent`，区分用户主动推进、append、同方向 reshuffle、后台 top-up、prefetch、恢复和 CLI one-shot。未携带合法 feed session 的旧调用一律走 baseline，不消费 Director slot。

### 8.2 IntentProjector

反馈先由确定性或受控分类器投影成可执行意图，不把原始 note 直接交给 Director：

```json
{
  "target_type": "topic_id",
  "target_id": "recommendation_feedback",
  "polarity": -1,
  "scope": "session",
  "confidence": 0.97,
  "expires_at": "2026-08-31T10:30:00Z",
  "source_event_id": 3905
}
```

`target_type` 枚举为 `item / creator / topic_id / style_key / content_form / duration_bucket / source_platform`，`scope` 为 `item / session / durable`。`content_form` 在分类能力未上线前只能是 `unknown`，不可被 Prompt 猜测。

明确的 item/creator/topic 排除先进入同步 session overlay，保护下一次 serve；不能等待 Planner。Director 只负责重新组织未来路径。

IntentProjector 每次真正改变 session intent 或 suppression 时，须在同一事务更新 `session_policy_revision + session_view_digest + state_revision` 并追加 `SESSION_INTENT_UPDATED(session_projection.v1)`；TTL 自动到期解除 suppression 时写 `SESSION_SUPPRESSION_EXPIRED`，hysteresis 达退出阈值解除时仍写 `SESSION_INTENT_UPDATED` 并记录受控 reason。原始 note 不入 DecisionLog，event detail 只保存受控 target ID、来源 event ID 和 pre/post digest。无语义变化不 bump revision，也不制造假事件。

### 8.3 Director Context Builder

负责生成稳定、紧凑、可缓存的输入，且必须：

- 将长期画像和会话状态放在不同字段；
- 只提供最近三个 batch 的聚合，不提供完整推荐标题列表；
- 从 topic taxonomy、recipe registry 和 fresh capability cube 生成 controlled catalog；
- 给所有词表项稳定 ID；
- 标注当前计划已提交、已曝光和待执行的 slot；
- 删除无关用户文本，截断用户明确输入；
- 生成下文锁定字段的 `planning_key`，并在完整 envelope 冻结后计算 `input_digest`；不另设语义不明的 context hash。

### 8.4 Director Planner

负责在受控动作空间中提出未来三-slot 计划。它不调用推荐工具，不执行 ReAct，不读取工具返回的候选列表。

Planner 的唯一成功结果是符合 schema 的 `DirectorProposal`。自然语言解释、markdown、工具调用或部分 JSON 均视为失败。Proposal 始终按不可信外部输入处理。

### 8.5 Plan Validator

分三层：

1. **Schema Validator**：类型、枚举、长度、必填字段、数值范围、未知字段拒绝。
2. **Semantic Validator**：配额可加和、catalog 存在、recipe/role 兼容、计划 basis 匹配、没有重复 slot key、没有非法自由文本。
3. **Feasibility Validator**：基于规划时完整 member/capability snapshot 检查单 slot 与三-slot 累计可行性；不是只检查 topic/style/source 各自的边际计数。

累计校验把每个候选在整个 horizon 最多使用一次，以前序 slot 已 shown/seen 的保守耗用运行 max-flow/匹配：所有 slot 的 lane minimum 必须存在无补货可行解，否则拒绝 Plan；target 只计算 cumulative coverage/shortfall code，不因未来可能补货而伪装成硬保证。该计算不把 item ID 写进 Plan、不预占库存，执行时每 slot 仍绑定 fresh ref；它只是防止“每屏单独可行、三屏合计必缺货”的计划通过。

Validator 可以执行有限的确定性规范化（排序、去空格、去重），不能猜测缺失语义。服务端在接受 Proposal 后生成 `plan_id / revision / timestamps / basis / recipe_version / role`；无法安全修复时整份提案失败并 fallback。

### 8.6 Plan/Slot Store

负责：

- 每个 StrategyStream 同时最多一个 `ACTIVE` plan；
- revision 单调递增；
- compare-and-swap 激活新计划；
- 保存 schema/model/prompt/recipe 版本；
- 计划 TTL、supersede、expire 和 failure reason；
- 拒绝 basis 已过期的异步 LLM 回包；
- 持久化 slot 的 `PENDING / LEASED / COMMITTED / SUBSTITUTED / PRESENTED / CLOSED / CANCELED / REVOKED` 状态；
- 使用数据库 CAS、lease、fencing token 和 request idempotency，不依赖进程内 `_serve_lock`；
- 提供 append-only audit，不原地覆盖历史计划 JSON。

### 8.7 Feedback Gate

纯规则组件。它不生成内容，只判断下一步控制动作：

- `CONTINUE`：计划仍有效，编译下一个 slot；
- `PATCH`：方向不变，但局部供给或约束需确定性修补；
- `REPLAN`：用户意图或计划基础发生实质变化，异步请求新计划；
- `FALLBACK`：不能安全执行计划，下一个 batch 使用当前基线推荐。

`PATCH` 不修改不可变 Plan。它把 GateDecision 中的版本化 patch overlay 绑定到下一个 `PENDING` slot，v1 只允许移除被显式 block 的 topic或启用该 slot 已声明的 fallback lane；每次都留痕并随 session revision 失效。v1 禁止 PATCH 切换 recipe/role/expression intent；需要换 recipe 时必须 REPLAN 或整批 FALLBACK，避免 planned/effective 归因分裂。

Gate 只写控制状态，不选择或提交内容。`REPLAN.serve_while_replanning` 与 `FALLBACK` 都只生成一个有 TTL、最多消费一次的 `next_batch_override`；后续 Executor 另取 fresh baseline/recovery snapshot 并持有自己的 batch reservation。已经 lease slot 后因 compiler/selector/verifier 失败而整批 baseline substitution 属于 8.10 的执行级 fallback，由 Composer/Executor 写 `FALLBACK_SERVED + SLOT_SUBSTITUTED`，不是 GateDecision。

### 8.8 Policy Compiler

将单个 leased `PlanSlot`，或一次已预留的 `STAY_SAME_DIRECTION` 请求，编译为 `CompiledPolicy`：

- 解析 topic lane 与 v1 style 软目标；
- 合并全局硬约束；
- 基于 fresh `SERVING` CandidateSetRef 的联合分布判断可执行性；
- 按声明顺序进行 shortage repair；
- 输出选择器可直接执行的整数合同；
- 生成 `compiler_trace`，解释哪些约束被满足、修补或回退。

### 8.9 Constrained Composer

Composer 是现有推荐引擎的扩展，而不是新 Agent：

- 在每个 policy lane 内沿用 `PoolCurator` 分数；
- lane 间按整数目标和 recipe 的确定性顺序选取；
- 继续使用 MMR、topic/style/broad-topic caps、amplification guard、seen/dislike/platform filters 和既有退让语义；
- v1 不实现 source/platform quota；平台只作为 scope 和现有跨平台补位行为；
- 用按 lane 定向读取的 Candidate Bus API 获取候选，不能只在当前 relevance top window 上加权；
- 不因 Director 配额绕过任何系统规则；
- 选择不足时只使用 compiler 声明的 fallback lane；
- recipe overlay 必须是 request-scoped immutable 参数，禁止修改共享 `PoolCurator._weights`。

### 8.10 FinalPolicyVerifier

在 MMR 和可访问入口替换之后，对真正要提交的 rows 做纯函数验收；同一 verifier 还必须在最终写事务内、基于最新权威 eligibility 复核后的完整 survivor set 再运行一次：

- item 全部来自绑定的 CandidateSetRef；
- 系统与显式用户约束零违规；
- 记录每条 lane 的 `fulfilled / relaxed / underfilled`，以及整批是否转为 `whole_batch_baseline_fallback`；
- 事务内不得先删除失效 item、再拿缩水结果蒙混提交；任一 item 最新复核失败或第二次验收失败都回滚整批 Director 事务，再走独立 baseline fallback。不能只验证 selector 前的临时结果。

v1 不允许“半批 Director、半批 baseline”的混合提交。若一次 `ADVANCE_PLAN / APPEND_PLAN` 已 lease 某 slot，但 lane minimum 无法满足，系统丢弃全部 Director tentative rows，沿用已预留的 request/batch identity 重新提交一个纯 baseline batch，并在同一事务把该 slot 终结为 `SUBSTITUTED`、推进 cursor；它占用的是时间线位置，但不宣称履行任何 Director lane，也不计 Director adherence。无 active slot 的普通 baseline/recovery 才不消费 cursor。这样既不会用未声明 topic 偷偷补配额、污染归因，也不会让下次 ADVANCE 无限重试同一语义 slot。

Whole-baseline 不能复用 Director 按 lane 定向构造的 SERVING ref。它必须从无 policy 的 normal baseline loader 重新取得 fresh snapshot，只共享最新 hard ConstraintSet 与 request claim；调用时设置 `fallback_depth=1 / director_hook=None`，禁止递归再次进入 Director fallback。Director 分支新增 all-or-nothing DAO/flag；普通 baseline 继续保留现有在硬约束后允许较短提交的语义，不能为实现 Director 偷改对照组。

当前 `PoolCurator` 的跨批疲劳已使用提交/展示历史，部分放大惩罚还以 `COALESCE(presented_at, created_at)` 计算。v1 保留这些 baseline 选择语义；新增的 `PRESENTED` 只决定因果曝光和方向 reward 归因，不暗中把全局疲劳查询改为仅看 ack。若以后重定义疲劳，必须另做迁移和实验，不能混入 Director rollout。

### 8.11 Expression 与 Supply Bridge

具体 item 选完后，表达层可以读取：

- `derived_role`；
- `expression_intent_code`；
- 当前 item 与用户画像的已验证关联；
- `rationale_codes`。

表达层不能改变 item 顺序或 admission。若文案生成失败，继续使用现有预计算文案或模板。表达与选片必须分别开关、分别实验。

Supply brief 在 v1 只允许 `topic_id + facet + reason_code + TTL`，且只作为 discovery 的异步 advisory demand：不能阻塞当前 serve，不能直接成为搜索 query，不能把 Director 自产供给误写成用户兴趣。自由文本新方向延后。

## 9. 受控词表

### 9.1 Topic taxonomy

当前上游 `topic_group` 是外部内容经过模型生成的文本，不能直接等于可执行词表。Director 前必须建立最小版本化 taxonomy：

```text
topic_id, canonical_label, aliases, taxonomy_version,
status(active/deprecated), adjacent_topic_ids
```

- prompt 和 Proposal 只使用稳定 `topic_id`；
- 外部 `topic_group` 先 canonicalize 并映射，未知值进入 `unknown`，不能成为指令；
- `bridge` 只能在 adjacency graph 有边时使用；没有边时 Validator 拒绝；
- taxonomy 更新不会修改旧计划语义，旧计划保留其版本。

每次 DirectorInput 还冻结服务端派生的 `profile_summary.anchor_topic_ids`。v1 adjacency entry 是无向、无权、只看一跳的 unordered pair，不做传递闭包。关系按该集合和同版本 graph 唯一计算：lane 全部 topic 都在 anchor set 才是 `ANCHOR`；全部不在 anchor、且每个 topic 都与至少一个 anchor 有直接边才是 `ADJACENT`；全部既非 anchor、又无 anchor 直接边才是 `NOVEL`。混合关系 lane 必须拆分或拒绝，不做多数票。positive topic 不自动等于本期 anchor；例如弱正向但未过 anchor 阈值的主题仍可位于当前探索边界。Proposal 不允许提供 relationship，Validator 在服务器盖章 Plan 时派生。

### 9.2 Role 与 recipe registry

v1 role 不由模型自由声明，而由 recipe registry 派生：

| recipe arm | 派生 role | 结构 | v1 约束（10 条基准） |
|------------|-----------|------|----------------------|
| `focused_practical` | `DEEPEN` | 在熟悉主题族内深入 | anchor 4–6；`hands_on + decision_support` 合计 target ≥ 4；单 style 仍服从既有 cap；novel ≤ 2 |
| `balanced_bridge` | `BRIDGE` | 熟悉与相邻主题桥接 | anchor 4–6；adjacent 2–4；novel 1–2；必须有 taxonomy 邻接边 |
| `novelty_probe` | `EXPLORE` | 有界试探新方向 | novel 2–3；anchor ≥ 4 |
| `recovery` | `RECOVER` | 明确冲突后的稳定恢复 | novel = 0；排除 session suppression；只选高供给 lane |
| `light_mix` | `LIGHTEN` | 降低认知密度 | `quick_scan + daily_wander + curiosity_spark` 合计 ≥ 4；单 style 仍服从既有 cap；anchor ≥ 4 |
| `safe_anchor` | `RECOVER` | 系统 fallback，不由 Planner 选择 | 至少 2 个 topic_id；不新增探索 |

`contrast_pair` 要求独立的立场/观点分类，`close` 要求可靠的 session-ending 意图；两者均延后。`confirm` 在 v1 复用 `focused_practical` 或 `safe_anchor`，等积累数据后再决定是否值得成为独立 arm。

每个 registry entry 必须包含：

```text
recipe_id + recipe_version
derived_role
allowed_lane_relationships
allowed_expression_intent_codes
min/target/max ranges
supported style preferences
relaxation ladder
compatible compiler/guardrail versions
unit-test fixture IDs
```

recipe 只控制配额、style 结构和 fallback 顺序；v1 的 `ScoringWeights` 与默认 MMR 参数保持不变。`10` 是基准；Director-aware v1 只接受 `1 <= requested_limit <= 20`，超界请求走显式 baseline/unsupported-limit reason，不进入 flow 或 Planner。

Recipe 的 style target 是跨多个合法 style 的系统侧软结构目标，不授权突破现有 `_style_cap`。若现有 cap 与 recipe target 同时不可满足，先保留 cap，并将 style target 记为 relaxed；不能借 selector 的“缺货退让”路径主动制造超 cap。

v1 兼容矩阵也是 registry 数据而非 prompt 约定：`focused_practical → PRACTICAL_DEEPEN`，`balanced_bridge → ENGINEERING_TO_FEEDBACK_BRIDGE`，`novelty_probe → BOUNDED_HCI_EXPLORATION`，`recovery/safe_anchor → SAFE_RECOVERY`，`light_mix → LIGHTEN_DENSITY`。模型给出 catalog 内但与所选 recipe 不兼容的 expression code，整份 Proposal 仍应拒绝，不能因为两个字符串分别合法就放行。

### 9.3 Style、content form 与 duration

- `style_key` 严格复用仓库 `VALID_STYLE_KEYS`，例如 `hands_on / deep_focus / quick_scan`；不存在 `paper_reading / practical_demo / case_study`。
- `style_key` 表达观看方式，不精确表达证据形态。若产品要支持“不要论文、要案例”，必须新增 `content_form = paper_explainer / case_study / hands_on_demo / commentary / unknown`，完成回填和质量评估后才可成为 Director 维度。
- duration 统一为：`SHORT = 1..300s`、`MEDIUM = 301..1500s`、`LONG > 1500s`、`UNKNOWN_OR_TEXT`；v1 只观测，不做硬配额。
- 未知分类不能自动当负面，也不能为了配额被静默排除。

所有受控值都使用 ID；展示 label 与可执行 ID 分离。所有自由文本均有长度上限、Unicode 控制字符清理和明确 data delimiter。

### 9.4 Expression intent 与 supply facet

v1 的 `expression_intent_code` 也是闭合 registry，例如：

```text
PRACTICAL_DEEPEN
ENGINEERING_TO_FEEDBACK_BRIDGE
BOUNDED_HCI_EXPLORATION
SAFE_RECOVERY
LIGHTEN_DENSITY
```

它只是表达层的意图标签，不是可执行 prompt 或自由文案。实际文字只能在 item 已选定后，结合可验证 evidence code 生成。Supply facet 同样只允许受控 topic/style/content-form ID；自由文本新方向、URL 和 query 均不属于 v1 合同。

### 9.5 Reason-code registry

所有 rationale、trigger、validation、repair、fallback、gate 和 lifecycle reason 都来自版本化 registry。只有 `catalog.rationale_codes` 是 LLM 可输出集合；其余 code 全由服务器派生。v1 示例允许的 Planner rationale 为：

```text
RECENT_POSITIVE_ANCHOR
ADJACENT_TOPIC_BRIDGE
BOUNDED_EXPLORATION
```

Input 和 Plan 都绑定 `reason_code_registry_version`。未知 code 直接拒绝，不把自由文本偷偷塞进 reason 字段；展示层需要文案时再由 code 映射本地模板。

每个 Planner rationale entry 还必须声明 typed evidence predicate：

| rationale | 必需 evidence 与目标约束 |
|---|---|
| `RECENT_POSITIVE_ANCHOR` | 至少一个未过期 `SESSION_DIRECTIVE` 或已 PRESENTED 的 `BATCH_RESPONSE`，polarity=POSITIVE，target 与本 slot 的 ANCHOR topic/style 相交 |
| `ADJACENT_TOPIC_BRIDGE` | `PROFILE_ANCHOR` + `TAXONOMY_EDGE`；edge 必须在 frozen graph 中直接连接 anchor 与本 slot 至少一个 ADJACENT topic |
| `BOUNDED_EXPLORATION` | `CAPABILITY_CELL` 覆盖本 slot NOVEL topics/minimum，且同 slot 的 ANCHOR minimum 已通过 capacity proof |

DirectorInput 的 evidence catalog 只提供这些受控 type/target/polarity/expiry，不带原文。Semantic Validator 对每个 rationale 验证 predicate、evidence target 与 slot lane/style 的交集；evidence ref 虽存在但类型、目标或时效不匹配仍拒绝。Expression 层在生成前再次验证 `recipe ↔ expression` 和 `rationale ↔ evidence`；失败时只用安全本地模板或不展示导语，绝不让错误理由进入文案。

## 10. 五个核心合同

所有合同采用：

- ISO 8601 UTC 时间；
- schema 版本显式保存；
- `additionalProperties: false`；
- 边界模型使用 strict、frozen 语义；ID 为服务端生成的 ULID 或同等不可猜测字符串；
- digest 为 RFC 8785 JSON Canonicalization Scheme（JCS）结果的 SHA-256，编码为小写 `sha256:<hex>`；
- reason 使用枚举 code，不保存或要求模型暴露 chain-of-thought。

所有自带 digest 的对象都使用明确的无自引用投影：`input_digest` 对移除自身字段后的完整 DirectorInput 求 hash；`plan_digest` 对移除自身字段后的完整 DirectorPlan 求 hash；`policy_digest` 对移除自身字段后的完整 CompiledPolicy 求 hash。CandidateSetRef 的 `member_digest / feature_digest / inventory_digest` 分别针对下文定义的成员、执行特征和量化供给投影，不是对整个 ref 求 hash。任何 schema 新字段若未显式排除，默认进入对应对象 digest；时间、null、数字和对象键序列化均由 JCS 唯一化，禁止各语言自行拼字符串。

### 10.1 CandidateSetRef

CandidateSetRef 是 Candidate Bus 的不可变引用。完整成员和冻结后的执行特征保存在服务端，引用及其 Planner 视图不携带候选 ID、标题、URL 或正文。

2026-09 的排序键迁移后，现有 `PoolCurator`/MMR 内部 score dict 使用 `DiscoveredContent.scoring_key`（正常候选等于平台限定的 `item_key`，旧缺失行才回退 `bvid`）。CandidateSetRef 成员、跨源去重与 recommendation 归因仍以 canonical `item_key` 为权威；prepared snapshot adapter 必须把每个可执行 item 的 `item_key ↔ scoring_key` 显式配对并校验唯一，不能直接用 legacy `bvid` 当跨平台成员 ID，也不能把两个不同 `item_key` 因旧评分键碰撞合并。

现有 `PoolDistributionSnapshot` 可以提供 deficit/saturation 起点，但只有边际分布，不能回答 “topic A + hands_on + 某平台 + 中时长” 是否同时有货；因此 CandidateSetRef builder 是新增能力，不是给旧 snapshot 换名字。

```json
{
  "schema_version": "candidate-set-ref.v1",
  "candidate_set_ref_id": "01K4CSET...",
  "purpose": "PLANNING",
  "created_at": "2026-08-31T10:00:00Z",
  "expires_at": "2026-08-31T10:20:00Z",
  "scope": {
    "profile_id": "default",
    "feed_session_id": "fs_01K4...",
    "surface": "desktop_web",
    "source_platform_scope": "all",
    "feed_mode": "director",
    "requested_limit": 10
  },
  "snapshot": {
    "pool_generation": 771,
    "feedback_cursor": 3901,
    "seen_cursor": 183,
    "exclusion_digest": "sha256:exc...",
    "profile_view_digest": "sha256:profile...",
    "recent_history_digest": "sha256:recent-history...",
    "scoring_context_digest": "sha256:scoring-context...",
    "eligibility_policy_version": "serve-guards.v8",
    "classification_schema_version": "content-class.v5",
    "topic_canonicalizer_version": "topic-canonicalizer.v1",
    "broad_topic_map_version": "broad-topic-map.v1",
    "selector_bucket_schema_version": "selector-buckets.v1",
    "curator_version": "pool-curator.v1",
    "embedding_model_version": "embedding.v1"
  },
  "member_count": 101,
  "member_digest": "sha256:members...",
  "feature_digest": "sha256:features...",
  "inventory_digest": "sha256:inventory...",
  "topic_taxonomy_version": "topic-taxonomy.v1",
  "style_catalog_version": "style-keys.v1",
  "stored_capability_cube_ref_id": "01K4CUBE...",
  "topic_marginals": {
    "agent_memory": 12,
    "agent_tooling": 11,
    "agent_observability": 8,
    "developer_workflows": 10,
    "local_ai_tooling": 8,
    "recommendation_feedback": 7,
    "feedback_systems": 6,
    "product_analytics": 6,
    "ai_product_design": 8,
    "human_ai_interaction": 5,
    "adaptive_interfaces": 4
  },
  "capability_cell_count": 27,
  "capability_cells_truncated": true,
  "capability_cells": [
    {
      "topic_id": "agent_memory",
      "style_key": "hands_on",
      "duration_bucket": "MEDIUM",
      "source_platform": "bilibili",
      "servable_count": 8,
      "quality_p50": 0.78,
      "quality_p90": 0.91,
      "recent_exposure_count": 1
    },
    {
      "topic_id": "recommendation_feedback",
      "style_key": "deep_focus",
      "duration_bucket": "LONG",
      "source_platform": "youtube",
      "servable_count": 3,
      "quality_p50": 0.74,
      "quality_p90": 0.87,
      "recent_exposure_count": 0
    },
    {
      "topic_id": "human_ai_interaction",
      "style_key": "decision_support",
      "duration_bucket": "MEDIUM",
      "source_platform": "bilibili",
      "servable_count": 2,
      "quality_p50": 0.67,
      "quality_p90": 0.78,
      "recent_exposure_count": 0
    }
  ],
  "unknown_counts": {
    "topic_id": 16,
    "style_key": 5,
    "duration_bucket": 11
  }
}
```

不变量：

- `purpose = PLANNING | SERVING`；Planner 只看前者，CompiledPolicy 只能绑定后者；
- `PLANNING` ref 默认 20 分钟，`SERVING` ref 默认 90 秒，均过期即不可新编译或提交；
- ref 创建后成员不可改变，池变化必须生成新 ref；
- capability cube 是联合分布，不允许用多个边际计数假装交集可行；
- v1 每个候选恰有一个 canonical primary `topic_id` 或 `unknown`；完整 topic marginals 与 `unknown_counts.topic_id` 之和必须等于 `member_count`。`capability_cells_truncated` 只表示 prompt 展示的联合 cells 被截断，不表示服务端 marginals 不完整；
- CandidateSetRef envelope 带完整 topic marginals、unknown counts、top cells 和 `stored_capability_cube_ref_id`；prompt 只放有界的 top cells 与这些 marginals，Feasibility Validator 通过 ref 使用服务端完整 sparse cube。完整 cube 不要求内嵌到 JSON/prompt，但 ref 缺失、digest 不符或 cell_count 不符都使 validation 失败；
- `member_digest` 覆盖 canonical-sorted `item_key` 身份；`feature_digest` 按 item_key 冻结 selector 实际读取的 canonical topic/style/duration/source、broad-topic/supergroup bucket、amplification keys、accessibility alias、原输入 ordinal、relevance_score、candidate_tier、last_scored_at/discovered_at、view_count、legacy bvid tie token、最终 request-scoped Curator score/score-component digest、MMR embedding ref/vector digest与缺失策略，以及 visual/profile/keyframe/danmaku bonus 的已计算结果和相应版本。profile/recent-history/scoring context digests 也进入 snapshot，确保同一 ref/policy 不会因实时重算分数或输入 list order 而漂移；`inventory_digest` 覆盖量化后的 capability/quality 分布，单个成员增减不必改变它；
- Director request 内所有 score/embedding/bonus map 以 canonical item_key 为键；builder 负责一次性把现有 bvid-keyed 数据映射并冻结。排序沿用版本化现有 tie fields，最后以 item_key 打破完全相等，不能运行时再读 live row；
- SERVING ref builder 在 freeze 前完成可访问入口解析；若提交前必须替换，只能使用 ref 内预先冻结的 canonical alias，未知替换视为 eligibility 失败并整批回滚。ref 建立后 selection 阶段只允许最新硬约束让结果失败，不能重新读取会改变排序的 live profile/history/bonus；
- 最终提交仍重新检查最新的安全、明确 dislike 和时效约束；snapshot 不是绕过新硬约束的许可证。

### 10.2 DirectorInput → DirectorProposal → DirectorPlan

这是最重要的信任边界：LLM 只能产生 Proposal；服务器校验后才生成正式 Plan。

#### 10.2.1 DirectorInput

Planner 输入是完整的受控 JSON；示例：

```json
{
  "schema_version": "director-input.v1",
  "planning_request_id": "01K4REQ...",
  "planning_key": "sha256:planning-key...",
  "input_digest": "sha256:input...",
  "mode": "ENFORCE",
  "requested_at": "2026-08-31T10:00:00Z",
  "accept_until": "2026-08-31T10:01:00Z",
  "trigger": {
    "type": "SESSION_START",
    "reason_codes": ["NO_ACTIVE_PLAN"],
    "feedback_cursor": 3901
  },
  "basis": {
    "profile_id": "default",
    "feed_session_id": "fs_01K4...",
    "surface": "desktop_web",
    "source_platform_scope": "all",
    "feed_mode": "director",
    "horizon_start_slot_seq": 18,
    "director_runtime_epoch": 12,
    "director_state_revision": 118,
    "profile_policy_revision": 42,
    "profile_view_digest": "sha256:profile...",
    "session_policy_revision": 17,
    "session_view_digest": "sha256:session...",
    "planning_candidate_set_ref_id": "01K4CSET...",
    "planning_inventory_digest": "sha256:inventory...",
    "topic_taxonomy_version": "topic-taxonomy.v1",
    "recipe_registry_version": "director-recipes.v1",
    "guardrail_policy_version": "serve-guards.v8",
    "reason_code_registry_version": "director-reasons.v1",
    "gate_policy_version": "director-gate.v1"
  },
  "delivery_contract": {
    "planning_horizon": 3,
    "commit_horizon": 1,
    "reference_batch_size": 10,
    "max_lanes_per_slot": 3
  },
  "catalog": {
    "topic_ids": [
      "agent_memory",
      "agent_tooling",
      "agent_observability",
      "developer_workflows",
      "local_ai_tooling",
      "recommendation_feedback",
      "feedback_systems",
      "product_analytics",
      "ai_product_design",
      "human_ai_interaction",
      "adaptive_interfaces"
    ],
    "topic_adjacency": [
      ["agent_tooling", "developer_workflows"],
      ["agent_tooling", "local_ai_tooling"],
      ["agent_memory", "recommendation_feedback"],
      ["agent_memory", "feedback_systems"],
      ["agent_tooling", "ai_product_design"],
      ["agent_tooling", "product_analytics"],
      ["feedback_systems", "ai_product_design"],
      ["ai_product_design", "human_ai_interaction"]
    ],
    "style_keys": [
      "deep_focus",
      "quick_scan",
      "hands_on",
      "decision_support",
      "story_immersion",
      "opinion_sparring",
      "social_chat",
      "daily_wander",
      "mood_release",
      "aesthetic_browse",
      "ambient_companion",
      "live_pulse",
      "curiosity_spark"
    ],
    "recipes": [
      {"recipe_id": "focused_practical", "recipe_version": "1", "derived_role": "DEEPEN"},
      {"recipe_id": "balanced_bridge", "recipe_version": "1", "derived_role": "BRIDGE"},
      {"recipe_id": "novelty_probe", "recipe_version": "1", "derived_role": "EXPLORE"},
      {"recipe_id": "recovery", "recipe_version": "1", "derived_role": "RECOVER"},
      {"recipe_id": "light_mix", "recipe_version": "1", "derived_role": "LIGHTEN"}
    ],
    "expression_intent_codes": [
      "PRACTICAL_DEEPEN",
      "ENGINEERING_TO_FEEDBACK_BRIDGE",
      "BOUNDED_HCI_EXPLORATION",
      "SAFE_RECOVERY",
      "LIGHTEN_DENSITY"
    ],
    "rationale_codes": [
      "RECENT_POSITIVE_ANCHOR",
      "ADJACENT_TOPIC_BRIDGE",
      "BOUNDED_EXPLORATION"
    ],
    "evidence_refs": [
      {
        "evidence_ref": "session:e91",
        "evidence_type": "SESSION_DIRECTIVE",
        "target_type": "style_key",
        "target_ids": ["hands_on"],
        "polarity": "POSITIVE",
        "expires_at": "2026-08-31T10:20:00Z"
      },
      {
        "evidence_ref": "batch:b88",
        "evidence_type": "BATCH_RESPONSE",
        "target_type": "topic_id",
        "target_ids": ["agent_memory", "agent_tooling"],
        "polarity": "POSITIVE",
        "expires_at": "2026-08-31T10:20:00Z"
      },
      {
        "evidence_ref": "taxonomy:edge_17",
        "evidence_type": "TAXONOMY_EDGE",
        "target_type": "topic_pair",
        "target_ids": ["agent_tooling", "product_analytics"],
        "polarity": "NEUTRAL",
        "expires_at": "2026-09-30T00:00:00Z"
      },
      {
        "evidence_ref": "profile:i_agent",
        "evidence_type": "PROFILE_ANCHOR",
        "target_type": "topic_id",
        "target_ids": ["agent_memory", "agent_tooling", "agent_observability"],
        "polarity": "POSITIVE",
        "expires_at": "2026-09-30T00:00:00Z"
      },
      {
        "evidence_ref": "pool:c_hci",
        "evidence_type": "CAPABILITY_CELL",
        "target_type": "topic_id",
        "target_ids": ["human_ai_interaction", "adaptive_interfaces"],
        "polarity": "NEUTRAL",
        "expires_at": "2026-08-31T10:20:00Z"
      }
    ],
    "forbidden_relaxations": [
      "SEEN_FILTER",
      "USER_EXPLICIT_BLOCK",
      "PLATFORM_SCOPE",
      "TEMPORAL_ELIGIBILITY",
      "AMPLIFICATION_GUARD"
    ]
  },
  "profile_summary": {
    "anchor_topic_ids": ["agent_memory", "agent_tooling", "agent_observability"],
    "positive_topics": [
      {"topic_id": "agent_memory", "strength": 0.91},
      {"topic_id": "agent_tooling", "strength": 0.86},
      {"topic_id": "ai_product_design", "strength": 0.67}
    ],
    "negative_topics": [],
    "preferred_styles": ["hands_on", "decision_support"],
    "exploration_openness": 0.55
  },
  "session_summary": {
    "normalized_directives": [
      {
        "target_type": "style_key",
        "target_id": "hands_on",
        "polarity": 1,
        "scope": "session",
        "confidence": 0.96,
        "evidence_ref": "session:e91"
      }
    ],
    "temporary_suppressions": [],
    "recent_batches": [
      {
        "batch_ref": "batch:b88",
        "role": "DEEPEN",
        "topic_counts": {"agent_memory": 2, "agent_tooling": 2},
        "positive_count": 2,
        "negative_count": 0,
        "presented": true
      }
    ]
  },
  "pool_capabilities": {
    "candidate_set_ref_id": "01K4CSET...",
    "inventory_digest": "sha256:inventory...",
    "servable_count": 101,
    "topic_counts_complete": true,
    "topic_counts": {
      "agent_memory": 12,
      "agent_tooling": 11,
      "agent_observability": 8,
      "developer_workflows": 10,
      "local_ai_tooling": 8,
      "recommendation_feedback": 7,
      "feedback_systems": 6,
      "product_analytics": 6,
      "ai_product_design": 8,
      "human_ai_interaction": 5,
      "adaptive_interfaces": 4
    },
    "unknown_topic_count": 16,
    "undercovered_topic_ids": ["human_ai_interaction", "adaptive_interfaces"],
    "capability_cell_count": 27,
    "capability_cells": [
      {
        "topic_id": "agent_memory",
        "style_key": "hands_on",
        "duration_bucket": "MEDIUM",
        "source_platform": "bilibili",
        "servable_count": 8,
        "quality_bucket": "HIGH"
      },
      {
        "topic_id": "recommendation_feedback",
        "style_key": "deep_focus",
        "duration_bucket": "LONG",
        "source_platform": "youtube",
        "servable_count": 3,
        "quality_bucket": "MEDIUM"
      },
      {
        "topic_id": "human_ai_interaction",
        "style_key": "decision_support",
        "duration_bucket": "MEDIUM",
        "source_platform": "bilibili",
        "servable_count": 2,
        "quality_bucket": "MEDIUM"
      }
    ],
    "capability_cells_truncated": true
  },
  "active_plan": null
}
```

原始点击流水仍用 cursor 审计；只有会影响 Director 的语义变化才递增 `profile_policy_revision` 或 `session_policy_revision`，避免 LLM 调用期间一次普通曝光就让回包永远过期。

`trigger.type` 枚举为 `SESSION_START / HORIZON_EXHAUSTED / EXPLICIT_FEEDBACK / INTENT_SHIFT / PROFILE_POLICY_CHANGED / CAPABILITY_SHIFT / PLAN_EXPIRED / MANUAL / RECOVERY`。

`planning_key` 由以下 canonical 字段计算并加唯一约束：

```text
mode + director_runtime_epoch + profile_id + feed_session_id + horizon_start_slot_seq
+ director_state_revision + profile_policy_revision + session_policy_revision
+ profile_view_digest + session_view_digest
+ active_plan_id/revision/digest/status (或 canonical null)
+ quantized inventory_digest + feedback_cursor
+ delivery contract + input/schema/taxonomy/recipe/guardrail/reason-code/Gate-policy versions
```

Proposal 回来时必须原样匹配 `planning_request_id + input_digest`。若只有 pool 成员变化而 capability digest 未变，可重新做 feasibility 后接受；taxonomy、recipe、guardrail、Gate policy、profile/session policy revision 变化则直接 `STALE_REJECTED`。

`input_digest` 由服务端在 payload 其余字段冻结后计算并写入；模型只负责原样回显，绝不要求模型自己实现 canonical hash。服务端还必须验证完整 topic marginals：本例 `85 + 16 == 101`。

`DirectorInput.mode` 只允许 `SHADOW | ENFORCE`；`OFF` 时根本不创建 Planner 请求。Proposal 不得自行声明 mode，服务器生成的 Plan 继承 Input mode。示例展示 ENFORCE；SHADOW 使用相同受控 payload 与校验合同，但生命周期严格隔离。

`active_plan` 是严格 nullable union。`SESSION_START / HORIZON_EXHAUSTED` 且没有活动计划时为 null；replan trigger 时必须是下列有界 summary，不能只给 plan ID 让模型猜剩余弧线：

```json
{
  "plan_id": "01K4PLAN...",
  "revision": 7,
  "plan_digest": "sha256:plan...",
  "mode": "ENFORCE",
  "director_runtime_epoch": 12,
  "status": "ACTIVE",
  "expires_at": "2026-08-31T10:20:03Z",
  "committed_through_slot_seq": 18,
  "next_slot_seq": 19,
  "remaining_slots": [
    {
      "slot_seq": 19,
      "slot_status": "PENDING",
      "role": "BRIDGE",
      "recipe_id": "balanced_bridge",
      "recipe_version": "1",
      "lane_summaries": [
        {"relationship": "ANCHOR", "topic_ids": ["agent_memory", "agent_tooling", "agent_observability"], "target": 4},
        {"relationship": "ADJACENT", "topic_ids": ["recommendation_feedback", "feedback_systems", "product_analytics"], "target": 4},
        {"relationship": "NOVEL", "topic_ids": ["human_ai_interaction", "adaptive_interfaces"], "target": 2}
      ],
      "expression_intent_code": "ENGINEERING_TO_FEEDBACK_BRIDGE"
    },
    {
      "slot_seq": 20,
      "slot_status": "PENDING",
      "role": "EXPLORE",
      "recipe_id": "novelty_probe",
      "recipe_version": "1",
      "lane_summaries": [
        {"relationship": "ANCHOR", "topic_ids": ["agent_memory", "agent_tooling", "agent_observability"], "target": 4},
        {"relationship": "ADJACENT", "topic_ids": ["product_analytics", "ai_product_design"], "target": 3},
        {"relationship": "NOVEL", "topic_ids": ["human_ai_interaction", "adaptive_interfaces"], "target": 3}
      ],
      "expression_intent_code": "BOUNDED_HCI_EXPLORATION"
    }
  ]
}
```

Summary 必须从当前 immutable Plan + PlanSlotState 服务器派生并与 digest 对上；只包含受控 IDs/计数。`remaining_slots` 最多 3，DELIVERED 时为空且 next 为 null。

`profile_summary.negative_topics` item 固定为 `{topic_id, strength, evidence_ref}`；`session_summary.temporary_suppressions` item 固定为 `{target_type, target_id, reason_code, source_event_id, created_at, expires_at}`，其中 target 必须能在相应 catalog 解析。空数组示例不意味着未定义类型；未知字段或自由 note 一律拒绝。

#### 10.2.2 不可信 DirectorProposal

模型只允许输出：

```json
{
  "schema_version": "director-proposal.v1",
  "planning_request_id": "01K4REQ...",
  "input_digest": "sha256:input...",
  "slots": [
    {
      "slot_seq": 18,
      "recipe_arm": "focused_practical",
      "lanes": [
        {
          "lane_key": "anchor_agent",
          "topic_ids": ["agent_memory", "agent_tooling", "agent_observability"],
          "min": 4,
          "target": 5,
          "max": 6,
          "fallback_lane_keys": []
        },
        {
          "lane_key": "adjacent_workflow",
          "topic_ids": ["developer_workflows", "local_ai_tooling"],
          "min": 2,
          "target": 3,
          "max": 4,
          "fallback_lane_keys": ["anchor_agent"]
        },
        {
          "lane_key": "adjacent_feedback",
          "topic_ids": ["recommendation_feedback", "feedback_systems"],
          "min": 1,
          "target": 2,
          "max": 2,
          "fallback_lane_keys": ["adjacent_workflow", "anchor_agent"]
        }
      ],
      "style_preferences": {
        "prefer": ["hands_on"],
        "avoid": ["deep_focus"]
      },
      "expression_intent_code": "PRACTICAL_DEEPEN",
      "rationale_codes": ["RECENT_POSITIVE_ANCHOR"],
      "evidence_refs": ["session:e91", "batch:b88"]
    },
    {
      "slot_seq": 19,
      "recipe_arm": "balanced_bridge",
      "lanes": [
        {
          "lane_key": "anchor_agent",
          "topic_ids": ["agent_memory", "agent_tooling", "agent_observability"],
          "min": 3,
          "target": 4,
          "max": 5,
          "fallback_lane_keys": []
        },
        {
          "lane_key": "bridge_feedback",
          "topic_ids": ["recommendation_feedback", "feedback_systems", "product_analytics"],
          "min": 3,
          "target": 4,
          "max": 5,
          "fallback_lane_keys": ["anchor_agent"]
        },
        {
          "lane_key": "novel_hci_seed",
          "topic_ids": ["human_ai_interaction", "adaptive_interfaces"],
          "min": 0,
          "target": 2,
          "max": 2,
          "fallback_lane_keys": ["bridge_feedback", "anchor_agent"]
        }
      ],
      "style_preferences": {"prefer": ["hands_on", "decision_support"], "avoid": []},
      "expression_intent_code": "ENGINEERING_TO_FEEDBACK_BRIDGE",
      "rationale_codes": ["ADJACENT_TOPIC_BRIDGE"],
      "evidence_refs": ["taxonomy:edge_17", "profile:i_agent"]
    },
    {
      "slot_seq": 20,
      "recipe_arm": "novelty_probe",
      "lanes": [
        {
          "lane_key": "familiar_anchor",
          "topic_ids": ["agent_memory", "agent_tooling", "agent_observability"],
          "min": 3,
          "target": 4,
          "max": 5,
          "fallback_lane_keys": []
        },
        {
          "lane_key": "adjacent_product",
          "topic_ids": ["product_analytics", "ai_product_design"],
          "min": 2,
          "target": 3,
          "max": 4,
          "fallback_lane_keys": ["familiar_anchor"]
        },
        {
          "lane_key": "novel_hci",
          "topic_ids": ["human_ai_interaction", "adaptive_interfaces"],
          "min": 1,
          "target": 3,
          "max": 3,
          "fallback_lane_keys": ["adjacent_product", "familiar_anchor"]
        }
      ],
      "style_preferences": {"prefer": ["quick_scan", "decision_support"], "avoid": []},
      "expression_intent_code": "BOUNDED_HCI_EXPLORATION",
      "rationale_codes": ["BOUNDED_EXPLORATION"],
      "evidence_refs": ["pool:c_hci"]
    }
  ]
}
```

正式 Proposal 必须像本 fixture 一样包含恰好三个完整 slot。模型不得输出 `plan_id / revision / timestamps / role / relationship / recipe_version / status / hard_filters / candidate IDs / tool calls`；relationship 由服务器根据 frozen anchor set 与 taxonomy graph 派生，不能让模型自报“新颖度”。

#### 10.2.3 服务器盖章后的 DirectorPlan

下面是完整、可校验的三-slot 示例：

```json
{
  "schema_version": "director-plan.v1",
  "plan_id": "01K4PLAN...",
  "revision": 7,
  "plan_digest": "sha256:plan...",
  "planning_key": "sha256:planning-key...",
  "planning_request_id": "01K4REQ...",
  "input_digest": "sha256:input...",
  "mode": "ENFORCE",
  "created_at": "2026-08-31T10:00:03Z",
  "expires_at": "2026-08-31T10:20:03Z",
  "basis": {
    "profile_id": "default",
    "feed_session_id": "fs_01K4...",
    "surface": "desktop_web",
    "source_platform_scope": "all",
    "feed_mode": "director",
    "horizon_start_slot_seq": 18,
    "director_runtime_epoch": 12,
    "director_state_revision": 118,
    "profile_policy_revision": 42,
    "profile_view_digest": "sha256:profile...",
    "session_policy_revision": 17,
    "session_view_digest": "sha256:session...",
    "feedback_cursor": 3901,
    "planning_candidate_set_ref_id": "01K4CSET...",
    "planning_inventory_digest": "sha256:inventory...",
    "topic_taxonomy_version": "topic-taxonomy.v1",
    "recipe_registry_version": "director-recipes.v1",
    "guardrail_policy_version": "serve-guards.v8",
    "reason_code_registry_version": "director-reasons.v1",
    "gate_policy_version": "director-gate.v1"
  },
  "planning_horizon": 3,
  "commit_horizon": 1,
  "reference_batch_size": 10,
  "validation_proof": {
    "proof_schema_version": "director-capacity-proof.v1",
    "validator_version": "director-plan-validator.v1",
    "planning_candidate_set_ref_id": "01K4CSET...",
    "planning_member_digest": "sha256:members...",
    "planning_feature_digest": "sha256:features...",
    "capacity_proof_digest": "sha256:capacity-proof...",
    "capacity_model": "UNIQUE_ITEM_CONSERVATIVE",
    "feasible_through_slot_seq": 20,
    "cumulative_minimum_feasible": true
  },
  "supersedes_plan_id": null,
  "preserve_through_slot_seq": null,
  "slots": [
    {
      "slot_seq": 18,
      "role": "DEEPEN",
      "recipe": {"recipe_id": "focused_practical", "recipe_version": "1"},
      "lanes": [
        {
          "lane_key": "anchor_agent",
          "relationship": "ANCHOR",
          "topic_ids": ["agent_memory", "agent_tooling", "agent_observability"],
          "min": 4,
          "target": 5,
          "max": 6,
          "fallback_lane_keys": []
        },
        {
          "lane_key": "adjacent_workflow",
          "relationship": "ADJACENT",
          "topic_ids": ["developer_workflows", "local_ai_tooling"],
          "min": 2,
          "target": 3,
          "max": 4,
          "fallback_lane_keys": ["anchor_agent"]
        },
        {
          "lane_key": "adjacent_feedback",
          "relationship": "ADJACENT",
          "topic_ids": ["recommendation_feedback", "feedback_systems"],
          "min": 1,
          "target": 2,
          "max": 2,
          "fallback_lane_keys": ["adjacent_workflow", "anchor_agent"]
        }
      ],
      "style_preferences": {
        "prefer": ["hands_on"],
        "avoid": ["deep_focus"]
      },
      "expression_intent_code": "PRACTICAL_DEEPEN",
      "rationale_codes": ["RECENT_POSITIVE_ANCHOR"],
      "evidence_refs": ["session:e91", "batch:b88"]
    },
    {
      "slot_seq": 19,
      "role": "BRIDGE",
      "recipe": {"recipe_id": "balanced_bridge", "recipe_version": "1"},
      "lanes": [
        {
          "lane_key": "anchor_agent",
          "relationship": "ANCHOR",
          "topic_ids": ["agent_memory", "agent_tooling", "agent_observability"],
          "min": 3,
          "target": 4,
          "max": 5,
          "fallback_lane_keys": []
        },
        {
          "lane_key": "bridge_feedback",
          "relationship": "ADJACENT",
          "topic_ids": ["recommendation_feedback", "feedback_systems", "product_analytics"],
          "min": 3,
          "target": 4,
          "max": 5,
          "fallback_lane_keys": ["anchor_agent"]
        },
        {
          "lane_key": "novel_hci_seed",
          "relationship": "NOVEL",
          "topic_ids": ["human_ai_interaction", "adaptive_interfaces"],
          "min": 0,
          "target": 2,
          "max": 2,
          "fallback_lane_keys": ["bridge_feedback", "anchor_agent"]
        }
      ],
      "style_preferences": {"prefer": ["hands_on", "decision_support"], "avoid": []},
      "expression_intent_code": "ENGINEERING_TO_FEEDBACK_BRIDGE",
      "rationale_codes": ["ADJACENT_TOPIC_BRIDGE"],
      "evidence_refs": ["taxonomy:edge_17", "profile:i_agent"]
    },
    {
      "slot_seq": 20,
      "role": "EXPLORE",
      "recipe": {"recipe_id": "novelty_probe", "recipe_version": "1"},
      "lanes": [
        {
          "lane_key": "familiar_anchor",
          "relationship": "ANCHOR",
          "topic_ids": ["agent_memory", "agent_tooling", "agent_observability"],
          "min": 3,
          "target": 4,
          "max": 5,
          "fallback_lane_keys": []
        },
        {
          "lane_key": "adjacent_product",
          "relationship": "ADJACENT",
          "topic_ids": ["product_analytics", "ai_product_design"],
          "min": 2,
          "target": 3,
          "max": 4,
          "fallback_lane_keys": ["familiar_anchor"]
        },
        {
          "lane_key": "novel_hci",
          "relationship": "NOVEL",
          "topic_ids": ["human_ai_interaction", "adaptive_interfaces"],
          "min": 1,
          "target": 3,
          "max": 3,
          "fallback_lane_keys": ["adjacent_product", "familiar_anchor"]
        }
      ],
      "style_preferences": {"prefer": ["quick_scan", "decision_support"], "avoid": []},
      "expression_intent_code": "BOUNDED_HCI_EXPLORATION",
      "rationale_codes": ["BOUNDED_EXPLORATION"],
      "evidence_refs": ["pool:c_hci"]
    }
  ],
  "advisory_supply_briefs": [
    {
      "topic_id": "human_ai_interaction",
      "facet": "hands_on",
      "reason_code": "PLANNED_LANE_UNDERCOVERED",
      "priority": "MEDIUM",
      "expires_at": "2026-08-31T11:00:00Z"
    }
  ]
}
```

`advisory_supply_briefs` 由服务器根据已接受 lane 与 capability deficit 确定性派生，不由 LLM 写搜索文字；feature flag 关闭时为空数组。

DirectorPlan 不变量：

- `planning_horizon == len(slots)`，v1 必须为 3；
- `commit_horizon == 1`；
- `validation_proof` 由服务器生成而非模型回显；candidate ref 必须等于 basis ref，member/feature digest 必须解析到该 ref，`feasible_through_slot_seq` 必须等于最后一个 slot，且 `cumulative_minimum_feasible` 只能为 true 才可盖章；
- `slot_seq` 连续且从 `horizon_start_slot_seq` 开始；
- 每个 slot `sum(target) == reference_batch_size`；
- 每个 slot `sum(min) <= reference_batch_size <= sum(max)`；
- `min <= target <= max`；
- 每个 lane 至少两个 topic_id，除非系统证明单 topic 目标不可能违反当前 cap；
- 所有 topic/style/recipe 必须来自输入 catalog；每个 evidence ref 必须按 `evidence_ref` 精确解析到 catalog 中未过期的 typed entry，并满足 rationale predicate；
- role 必须等于 recipe registry 派生值；recipe version 必须精确匹配；
- lane relationship 必须由 Input 的 frozen anchor set + taxonomy adjacency 确定性派生；每个 lane 内 topic 必须同关系，模型不能自报或覆盖；
- fallback 不能形成环；
- topic adjacency、规划时联合供给和全局 cap 必须可行；
- 三-slot cumulative minimum 在候选跨 horizon 唯一使用的保守模拟下必须可行。服务器对 `proof_schema_version + candidate digests + slot/lane minima + cap/constraint versions + 每 slot 匿名残余容量矩阵` 的 canonical artifact 求 `capacity_proof_digest`；artifact 不含 item ID/标题，但必须随 Plan 保存到 `validation_proof` 并进入 `plan_digest`。target coverage 不足可降级/重规划，但不能把补货当作 minimum 成立条件；
- Plan 本身不保存 `ACTIVE / COMMITTED` 等可变状态；状态在 PlanEnvelope、PlanSlotState 和 DecisionLog；
- 已有 `COMMITTED / SUBSTITUTED / PRESENTED / CLOSED` 执行结果的 slot 永不改写；新 plan 可以 cancel 尚为 `PENDING` 的 slot，或通过递增 fencing token revoke 正在 `LEASED` 但尚未提交的 slot；
- 相同 `planning_key` 只接受第一份合法结果；异步回包 basis 过期则记录 `STALE_REJECTED`。
- 初始/新 horizon Plan 要求 `supersedes_plan_id = preserve_through_slot_seq = null`；replan Plan 要求二者同时非空，`supersedes_plan_id` 精确等于激活事务开始时的 current ACTIVE/DELIVERED pointer，`preserve_through_slot_seq` 等于该 session 的 committed-through（包含 SUBSTITUTED 时间线位置），且 `horizon_start_slot_seq = preserve_through_slot_seq + 1`。旧 plan 的 terminal slots 不复制、不重建；若旧 Plan 已 DELIVERED，激活事务还须关闭其剩余控制反馈窗口并把它置 SUPERSEDED；任一边界不符则 stale reject；

`mode = SHADOW` 的合法 Plan 只以 `SHADOW_VALIDATED → SHADOW_EVALUATED` envelope 保存：不写 active plan pointer、不创建可 lease slot、不改变 DirectorSessionState，也不能被 enforce API 读取。切换到 enforce 后必须用新的 ENFORCE DirectorInput/planning_key 重新规划；禁止把历史 shadow artifact“升格”执行。只有 ENFORCE Plan 才能走 `VALIDATED → ACTIVE`。

### 10.3 CompiledPolicy

CompiledPolicy 是唯一允许进入 enforce 热路径的 Director 产物，并绑定精确 `SERVING` CandidateSetRef。它的 binding 是判别联合：正常推进使用 `PLAN_SLOT`，同方向换批使用 `STAY_PATCH`。v1 中它的 `mode` 必须为 `ENFORCE`；shadow 使用不可提交的 `ShadowPolicyEvaluation`，不能伪造执行 lease：

```json
{
  "schema_version": "compiled-policy.v1",
  "compiled_policy_id": "01K4POL...",
  "compile_key": "sha256:compile...",
  "policy_digest": "sha256:policy...",
  "compiled_at": "2026-08-31T10:00:04Z",
  "expires_at": "2026-08-31T10:00:29Z",
  "mode": "ENFORCE",
  "binding": {
    "binding_type": "PLAN_SLOT",
    "profile_id": "default",
    "feed_session_id": "fs_01K4...",
    "request_id": "req_01K4...",
    "director_runtime_epoch": 12,
    "expected_director_state_revision": 119,
    "plan_id": "01K4PLAN...",
    "plan_revision": 7,
    "plan_digest": "sha256:plan...",
    "slot_seq": 18,
    "slot_lease_fencing_token": 4,
    "slot_lease_expires_at": "2026-08-31T10:00:30Z",
    "gate_decision_id": null,
    "patch_overlay_digest": null,
    "gate_valid_until": null,
    "enabled_fallback_lane_keys": [],
    "candidate_set_ref_id": "01K4SERVE...",
    "candidate_member_digest": "sha256:members...",
    "candidate_feature_digest": "sha256:features..."
  },
  "versions": {
    "compiler_version": "director-compiler.v1",
    "recipe_id": "focused_practical",
    "recipe_version": "1",
    "baseline_ranker_version": "pool-curator.v1",
    "guardrail_policy_version": "serve-guards.v8",
    "topic_taxonomy_version": "topic-taxonomy.v1"
  },
  "requested_limit": 10,
  "configured_policy_ttl_seconds": 90,
  "lanes": [
    {
      "lane_key": "anchor_agent",
      "relationship": "ANCHOR",
      "eligible_topic_ids": ["agent_memory", "agent_tooling", "agent_observability"],
      "minimum_count": 4,
      "target_count": 5,
      "maximum_count": 6,
      "available_before_select": 19,
      "fallback_lane_keys": []
    },
    {
      "lane_key": "adjacent_workflow",
      "relationship": "ADJACENT",
      "eligible_topic_ids": ["developer_workflows", "local_ai_tooling"],
      "minimum_count": 2,
      "target_count": 3,
      "maximum_count": 4,
      "available_before_select": 11,
      "fallback_lane_keys": ["anchor_agent"]
    },
    {
      "lane_key": "adjacent_feedback",
      "relationship": "ADJACENT",
      "eligible_topic_ids": ["recommendation_feedback", "feedback_systems"],
      "minimum_count": 1,
      "target_count": 2,
      "maximum_count": 2,
      "available_before_select": 7,
      "fallback_lane_keys": ["adjacent_workflow", "anchor_agent"]
    }
  ],
  "trusted_constraints": {
    "constraint_set_ref_id": "01K4CONS...",
    "constraint_schema_version": "serve-constraints.v1",
    "source": ["CODE", "USER_EXPLICIT"],
    "constraint_digest": "sha256:constraints...",
    "expires_at": "2026-08-31T10:00:30Z"
  },
  "director_preferences": {
    "preferred_styles": ["hands_on"],
    "avoided_styles": ["deep_focus"],
    "strength": "SOFT"
  },
  "recipe_style_structure": [
    {
      "style_group_id": "practical.v1",
      "eligible_style_keys": ["hands_on", "decision_support"],
      "target_count": 4,
      "strength": "SOFT",
      "per_style_cap_source": "serve-guards.v8"
    }
  ],
  "selector": {
    "ranker_ref": "pool-curator.v1",
    "mmr_profile_ref": "baseline-mmr.v1",
    "scoring_overlay": "NONE"
  },
  "compiler_trace": {
    "trace_schema_version": "director-compiler-trace.v1",
    "result": "EXACT",
    "step_codes": [
      "IDENTITY_SCALE",
      "MINIMUMS_RESERVED",
      "TARGETS_FEASIBLE"
    ],
    "repair_codes": []
  },
  "shortfall_policy": [
    "USE_LANE_FALLBACKS",
    "RELAX_DIRECTOR_STYLE_PREFERENCES",
    "RETURN_FEWER_IF_ALL_MINIMUMS_HOLD",
    "WHOLE_BATCH_BASELINE_IF_ANY_MINIMUM_FAILS"
  ],
  "compile_status": "EXACT",
  "degradation_codes": []
}
```

不变量：

- `binding_type = PLAN_SLOT` 时必须有 `plan_id/revision/digest + slot_seq + slot_lease_fencing_token/expires_at`，且 slot 当前为同 token 的 `LEASED`；
- `binding_type = STAY_PATCH` 时这些 slot lease 字段必须缺失，改为携带 `origin_plan_id/revision/digest + origin_slot_seq + origin_batch_instance_id + patch_sequence + batch_reservation_id + reservation_fencing_token/expires_at`。`batch_reservation_id` 是请求幂等事务创建的短租约，只保护这次 batch，不改变原 slot 状态或 plan cursor；
- 两种 binding 都必须带 `director_runtime_epoch/profile_id/feed_session_id/request_id/expected_director_state_revision`。Final commit 按判别类型分别检查 slot lease 或 batch reservation；类型与字段不匹配直接拒绝；

- 每个 PlanLane `0 <= minimum <= target <= maximum <= requested_limit`；
- `sum(minimum) <= requested_limit <= sum(maximum)` 且 targets 总和等于 limit；
- 代码和用户显式约束只能保持或收紧；整批 baseline fallback 走既有全部 guardrails，并作为独立 batch 留痕；
- `compile_key` 必须覆盖所有会改变 binding 或语义输出的字段：

```text
binding_type + director_runtime_epoch + profile_id + feed_session_id + request_id
+ expected_director_state_revision
+ execution origin (plan_id/revision/digest + slot_seq [+ origin_batch/patch_sequence])
+ execution lease (slot lease 或 batch reservation 的 id/fencing_token/expires_at)
+ gate_decision_id + patch_overlay_digest + gate_valid_until + enabled_fallback_lane_keys
+ candidate_set_ref_id + candidate_member_digest + candidate_feature_digest
+ candidate_ref_expires_at + requested_limit
+ constraint_set_ref_id + trusted constraint_digest + constraint expiry
+ compiler/recipe/ranker/guardrail/taxonomy versions
+ configured_policy_ttl_seconds + mode
```

  服务端对该 canonical 投影求 hash并加唯一约束。首次成功编译原子持久化完整 artifact；相同 key 的重试只返回已存 artifact，不能重发 ID 或时间戳不同的“等价”对象；
- `expires_at = min(execution_lease_expires_at, serving_ref.expires_at, constraint_ref.expires_at, plan.expires_at, gate_valid_until_if_patched, compiled_at + configured_policy_ttl)`；`execution_lease` 对 PLAN_SLOT 是 slot lease，对 STAY_PATCH 是 batch reservation。任一上游期限缩短都必须产生新的 key/binding，不能复活旧 policy；
- policy、plan、lease 或 CandidateSetRef 过期后不得开始新 serve；
- 无 PATCH 时 `gate_decision_id / patch_overlay_digest / gate_valid_until` 均为 null 且 enabled 集为空；有 PATCH 时三者必填，编译与最终提交都要求当前时间不晚于 gate validity，enabled 集必须与 Gate overlay 的 registry 合法集合完全一致。这样同一 plan/ref 在不同或过期 overlay 下不可能复用旧 policy；
- lease 前还要求 `now + min_compilation_commit_budget <= gate_valid_until`；否则不取得 lease，直接走下文 `PATCH_OVERLAY_EXPIRED` fenced transition。已经 LEASED 的 slot 把 lease expiry clamp 到 gate validity，final commit 仍逐时钟检查，不能因“曾在有效期内开始”而越期提交；
- selector 实际内容只能来自绑定 ref；最终提交前仍需 FinalPolicyVerifier；
- 任意 repair/degradation 必须留 reason code，不能静默改变计划；
- `recipe_style_structure` 完全由 recipe registry 派生，单 style 仍受 guardrail cap；达不到软 target 时只记录 relaxation，不能通过 cap fallback 主动超额；
- v1 `scoring_overlay = NONE`，不修改 `ScoringWeights`。

Shadow 模式只生成 `ShadowPolicyEvaluation`：它记录 `evaluation_key`、plan/slot/ref digests、虚拟 realized mix、degradation、延迟与成本，但没有 `compiled_policy_id`、lease owner/token，也不能被 Composer commit API 接受。由类型和 DAO 双重隔离 shadow 与 enforce，而不是依赖调用方记得“不落库”。

`ConstraintSetRef` 是服务端不可变的 typed payload，冻结本次请求适用的 seen、explicit dislike/block、platform scope、temporal eligibility、amplification 和 admission 约束；CompiledPolicy 保存 ref、schema、digest 与 TTL，Composer 通过 ref 解析，不靠不可执行的 hash 猜规则。它不进 Planner prompt，过期或解析失败时整批 fail closed 到 baseline。`compiler_trace` 只使用受控 step/repair code 和数值统计，禁止自由文本。

`SAFE_RECOVERY` 不是第三种 CompiledPolicy binding。它走服务端拥有的 `BaselineRecoveryPolicy`：只在既有 baseline selector 上应用 fresh ConstraintSetRef、session suppression 和 `safe_anchor` 高供给 preset，通过 batch reservation 保证 request 幂等，但没有 plan/slot 归属，也不消费 Director cursor。Batch 明确标 `mode = recovery`，实验分析不能把它算作 Director adherence。

#### 10.3.1 `STAY_PATCH` alternative binding

同一个 CompiledPolicy schema 在 `binding_type = STAY_PATCH` 时，binding 的完整形状改为：

```json
{
  "binding_type": "STAY_PATCH",
  "profile_id": "default",
  "feed_session_id": "fs_01K4...",
  "request_id": "req_01K4_STAY...",
  "director_runtime_epoch": 12,
  "expected_director_state_revision": 120,
  "origin_plan_id": "01K4PLAN...",
  "origin_plan_revision": 7,
  "origin_plan_digest": "sha256:plan...",
  "origin_slot_seq": 18,
  "origin_batch_instance_id": "01K4BATCH18...",
  "patch_sequence": 1,
  "batch_reservation_id": "01K4RES...",
  "reservation_fencing_token": 1,
  "reservation_expires_at": "2026-08-31T10:05:30Z",
  "gate_decision_id": null,
  "patch_overlay_digest": null,
  "gate_valid_until": null,
  "enabled_fallback_lane_keys": [],
  "candidate_set_ref_id": "01K4SERVE_STAY...",
  "candidate_member_digest": "sha256:members-stay...",
  "candidate_feature_digest": "sha256:features-stay..."
}
```

`patch_sequence` 对 origin slot 单调，不能超过配置上限；候选必须排除 origin batch 及先前 patch batches。创建 batch reservation 与占用 request id 在同一事务完成；过期 reservation 只能 CAS 失败或重建为更高 fencing token，不能复用旧 policy。

#### 10.3.2 ConstraintSetRef

```json
{
  "schema_version": "constraint-set-ref.v1",
  "constraint_set_ref_id": "01K4CONS...",
  "created_at": "2026-08-31T10:00:04Z",
  "expires_at": "2026-08-31T10:00:30Z",
  "scope": {
    "profile_id": "default",
    "feed_session_id": "fs_01K4...",
    "request_id": "req_01K4...",
    "surface": "desktop_web",
    "source_platform_scope": "all"
  },
  "snapshot": {
    "seen_cursor": 183,
    "explicit_feedback_cursor": 3901,
    "profile_policy_revision": 42,
    "session_policy_revision": 17,
    "eligibility_policy_version": "serve-guards.v8"
  },
  "constraint_digest": "sha256:constraints...",
  "payload": {
    "seen_policy": "EXCLUDE",
    "excluded_item_set_ref_id": "01K4EXCL...",
    "amplification_guard_set_ref_id": "01K4AMP...",
    "amplification_guard_digest": "sha256:amplification-guard...",
    "blocked_topic_ids": [],
    "blocked_style_keys": [],
    "source_platform_scope": "all",
    "engine_source_platform_token": "",
    "temporal_policy_ref": "temporal.v3",
    "amplification_policy_ref": "amplification.v2",
    "admission_policy_ref": "admission.v5"
  }
}
```

`constraint_digest` 对 `scope + snapshot + payload` 求 hash，不含 ref ID、自身 digest 和 envelope 时间；TTL 另行进入 compile key。`excluded_item_set_ref_id` 在服务端解析为 exact canonical item_key set，不能只存 digest；`amplification_guard_set_ref_id` 同样解析为本请求实际 guard keys，候选自身 amplification keys 已冻结在 CandidateSet feature payload，二者共同执行 cap。Final commit 仍叠加比 snapshot 更新的 explicit block、安全和时效约束；新硬约束只能让本批失败，不能被冻结 ref 屏蔽。

#### 10.3.3 ShadowPolicyEvaluation

```json
{
  "schema_version": "shadow-policy-evaluation.v1",
  "shadow_evaluation_id": "01K4SHADOW...",
  "evaluation_key": "sha256:shadow-eval...",
  "evaluated_at": "2026-08-31T10:00:05Z",
  "mode": "SHADOW",
  "binding": {
    "profile_id": "default",
    "feed_session_id": "fs_01K4...",
    "planning_request_id": "01K4REQ_SHADOW...",
    "director_runtime_epoch": 12,
    "input_digest": "sha256:input-shadow...",
    "plan_id": "01K4PLAN_SHADOW...",
    "plan_revision": 7,
    "plan_digest": "sha256:plan-shadow...",
    "slot_seq": 18,
    "candidate_set_ref_id": "01K4SERVE_SHADOW...",
    "candidate_member_digest": "sha256:members-shadow...",
    "candidate_feature_digest": "sha256:features-shadow...",
    "constraint_set_ref_id": "01K4CONS_SHADOW...",
    "constraint_digest": "sha256:constraints-shadow..."
  },
  "versions": {
    "compiler_version": "director-compiler.v1",
    "recipe_registry_version": "director-recipes.v1",
    "guardrail_policy_version": "serve-guards.v8",
    "topic_taxonomy_version": "topic-taxonomy.v1",
    "ranker_version": "pool-curator.v1"
  },
  "virtual_policy_digest": "sha256:virtual-policy...",
  "result": {
    "compile_status": "EXACT",
    "virtual_realized_lane_counts": {
      "anchor_agent": 5,
      "adjacent_workflow": 3,
      "adjacent_feedback": 2
    },
    "hard_violation_count": 0,
    "degradation_codes": [],
    "duration_ms": 71,
    "can_commit": false
  }
}
```

evaluation key 覆盖 director runtime epoch、shadow plan/ref/constraint digests 与 compiler/recipe/guardrail versions。Schema 根本没有 execution lease 或 compiled_policy_id；Composer 的 commit method 只接受 enforce/recovery execution union，静态类型和 runtime discriminator 都拒绝本对象。

#### 10.3.4 BaselineRecoveryPolicy

```json
{
  "schema_version": "baseline-recovery-policy.v1",
  "recovery_policy_id": "01K4RECOV...",
  "recovery_key": "sha256:recovery...",
  "created_at": "2026-08-31T10:04:01Z",
  "expires_at": "2026-08-31T10:04:30Z",
  "mode": "RECOVERY",
  "binding": {
    "profile_id": "default",
    "feed_session_id": "fs_01K4...",
    "request_id": "req_01K4_RECOVERY...",
    "director_runtime_epoch": 12,
    "expected_director_state_revision": 123,
    "consumed_gate_decision_id": "01K4GATE...",
    "override_mode": "SAFE_RECOVERY",
    "override_valid_until": "2026-08-31T10:06:00Z",
    "override_consumed_state_revision": 123,
    "profile_policy_revision": 42,
    "profile_view_digest": "sha256:profile...",
    "topic_taxonomy_version": "topic-taxonomy.v1",
    "anchor_topic_ids": ["agent_memory", "agent_tooling", "agent_observability"],
    "anchor_set_digest": "sha256:recovery-anchor-set...",
    "batch_reservation_id": "01K4RES_RECOVERY...",
    "reservation_fencing_token": 1,
    "reservation_expires_at": "2026-08-31T10:04:30Z",
    "candidate_set_ref_id": "01K4SERVE_RECOVERY...",
    "candidate_member_digest": "sha256:members-recovery...",
    "candidate_feature_digest": "sha256:features-recovery...",
    "candidate_ref_expires_at": "2026-08-31T10:04:45Z",
    "constraint_set_ref_id": "01K4CONS_RECOVERY...",
    "constraint_digest": "sha256:constraints-recovery...",
    "constraint_expires_at": "2026-08-31T10:04:40Z"
  },
  "requested_limit": 10,
  "selector_ref": "pool-curator.v1",
  "preset_ref": "safe-anchor.v1",
  "novelty_allowed": false,
  "consumes_director_slot": false
}
```

`anchor_topic_ids` 是服务器从绑定的 stable RecommendationProfileView 解析出的非空集合，并按 frozen taxonomy ordinal canonicalize；`anchor_set_digest` 对 `profile_policy_revision + profile_view_digest + topic_taxonomy_version + anchor_topic_ids` 求 hash。CandidateSetRef 的 frozen taxonomy features 必须用同一 taxonomy version，`novelty_allowed=false` 表示 recovery selector 只接受相对该 frozen set 为 ANCHOR 的 item，不能在执行时重新读取 live profile 猜 ANCHOR/NOVEL。若 anchor 供给不足以创建 policy，跳过 SAFE_RECOVERY，改走不宣称 anchor/novelty 的 NORMAL_BASELINE 或明确较短 baseline。

Recovery key 覆盖 director runtime epoch、consumed Gate/override mode+validity+consumption revision、request/reservation token+expiry、candidate ID/digests+expiry、constraint ID/digest+expiry、profile revision/view digest、taxonomy/anchor set、preset/selector/state revision 与 policy version。`expires_at = min(override_valid_until, reservation_expires_at, candidate_ref_expires_at, constraint_expires_at, created_at + recovery_policy_ttl)`。它只能执行代码拥有的 safe preset，不能接受 LLM 字段；若 recovery 自身失败，取消 recovery reservation、保留 generation claim 与 consumed provenance，转 fresh NORMAL_BASELINE 或明确较短 baseline。

### 10.4 FeedbackGateDecision

```json
{
  "schema_version": "feedback-gate-decision.v1",
  "gate_decision_id": "01K4GATE...",
  "gate_key": "sha256:gate...",
  "created_at": "2026-08-31T10:04:00Z",
  "valid_until": "2026-08-31T10:06:00Z",
  "basis": {
    "profile_id": "default",
    "feed_session_id": "fs_01K4...",
    "director_runtime_epoch": 12,
    "expected_director_state_revision": 121,
    "profile_policy_revision": 42,
    "session_policy_revision": 18,
    "session_view_digest": "sha256:session-after-suppression...",
    "active_plan_id": "01K4PLAN...",
    "active_plan_revision": 7,
    "active_plan_digest": "sha256:plan...",
    "active_plan_expires_at": "2026-08-31T10:20:03Z",
    "committed_through_slot_seq": 18,
    "next_slot_seq": 19,
    "feedback_cursor_after": 3901,
    "feedback_cursor_through": 3905,
    "feedback_batch_ref_id": "01K4FBATCH...",
    "feedback_event_set_digest": "sha256:feedback-event-set...",
    "pool_generation": 775,
    "inventory_digest": "sha256:inventory-gate...",
    "topic_taxonomy_version": "topic-taxonomy.v1",
    "guardrail_policy_version": "serve-guards.v8",
    "reason_code_registry_version": "director-reasons.v1",
    "gate_policy_version": "director-gate.v1"
  },
  "evidence": {
    "normalized_signals": [
      {
        "target_type": "topic_id",
        "target_id": "recommendation_feedback",
        "polarity": -1,
        "scope": "session",
        "confidence": 0.97,
        "source_event_id": 3905
      }
    ],
    "presented_batch_ids": ["01K4BATCH18..."],
    "quota_shortfall_codes": []
  },
  "action": "REPLAN",
  "reason_codes": ["EXPLICIT_SESSION_INTENT_CONFLICT"],
  "strength": "STRONG",
  "effect": {
    "kind": "REPLAN",
    "invalidate_from_slot_seq": 19,
    "enqueue_replan": true,
    "serve_while_replanning": "SAFE_RECOVERY",
    "next_batch_override_max_uses": 1
  }
}
```

`action` 枚举为 `CONTINUE / PATCH / REPLAN / FALLBACK`，并必须与 `effect.kind` 相同。`effect` 不是一组可随意 null 的平面字段，而是严格判别联合：

| action/effect.kind | 必填 effect | 禁止字段 | active plan |
|---|---|---|---|
| `CONTINUE` | `next_slot_seq` | patch、invalidate、fallback | 必须存在且未过期 |
| `PATCH` | `target_slot_seq + patch_overlay` | invalidate、enqueue_replan、fallback | 必须存在；目标只能是 PENDING |
| `REPLAN` | `invalidate_from_slot_seq + enqueue_replan=true + serve_while_replanning + next_batch_override_max_uses=1` | patch、fallback_mode、slot/token | 必须存在；若不存在计划应使用 Planner trigger 而非伪造 invalidate |
| `FALLBACK` | `fallback_mode + enqueue_replan + next_batch_override_max_uses=1` | patch、invalidate、slot/token | 必须为空；有 active plan 且失效时必须用 REPLAN |

各 variant 使用 `additionalProperties: false`。`basis.active_plan_*` 对 CONTINUE/PATCH/REPLAN 必填，对无计划 FALLBACK 全部为 null；不能只填一半。

`strength` 枚举为 `WEAK / MEDIUM / STRONG / HARD`；`serve_while_replanning` 与 `fallback_mode` 只允许 `NORMAL_BASELINE / SAFE_RECOVERY`。Gate 的 FALLBACK 永远是 `NO_SLOT`：禁止 plan slot、lease request、fencing token 或 cursor advance 字段。`enqueue_replan` 是显式布尔值；true 时 outbox 与 Gate apply 同事务。若已有 execution lease，本次既不成功提交 Director、也不成功提交 whole baseline，这只是 execution failure：释放/终态化 request，不创建一个“已应用”的 GateDecision。

不变量：

- Gate 使用归一化事件和计划归因，不读取 LLM 自由解释；
- `gate_key` 覆盖 director runtime epoch、profile/session、expected state revision、active plan id/revision/digest/expiry、feedback batch ref/event-set digest/cursor range、profile/session policy revision + session_view_digest、pool generation/inventory digest及 taxonomy/guardrail/reason registry/Gate policy version；服务端对该 canonical 投影求 hash。Gate 采用 create-and-apply 单事务：成功 artifact 的 key 唯一，相同 key 重放只返回已应用结果；事务回滚不留下占位行，因此不会让过期未应用 decision 永久挡住重算；
- `invalidate_from_slot_seq` 必须大于已 committed slot；
- `PATCH` 时 `patch_overlay` 必须是 `{blocked_topic_ids, enable_fallback_lane_keys}` 的 registry 合法子集；不得出现 recipe/role/expression 字段，其它 action 禁止出现 overlay；
- session suppression 在构造 GateDecision 之前已由 IntentProjector 同步写入，并有 TTL；Gate 必须绑定写入后的 state/policy revision；
- Gate 不直接改长期画像；
- `REPLAN` 不阻塞热路径；新计划未到时用 safe recovery 或 baseline；
- late feedback 可进入长期学习，但不能无条件打断已经转向的新 session；
- Gate 在短 `BEGIN IMMEDIATE` 内重读 basis、构造/校验 decision、确认 `now <= valid_until`，再 CAS `expected_director_state_revision`；decision/event/state/slot mutations、一次性 `next_batch_override` 与 replan outbox 同事务提交，但事务内绝不运行 selector 或提交 recommendation rows。CAS 或 TTL 失败则整事务回滚并另记不占 gate_key 的 operational stale counter，不保存一个“已决定但未应用”的权威 artifact；
- `PATCH` 同事务把 `applied_gate_decision_id + patch_overlay_digest` 写到目标 PENDING PlanSlotState；`REPLAN` 同事务设置 `replan_pending_version/invalidate_from_slot_seq`、cancel PENDING、revoke LEASED 并 bump fencing；若 revoked lease 绑定尚为 SELECTING_DIRECTOR 的 request claim，同时转成 BASELINE_FALLBACK_PENDING 并清除其 Director binding。Lease 查询只接受未 invalidated 且 overlay digest 与 session state 一致的 PENDING slot，因此旧方向不能在 Gate 后漏 lease，而等待中的原请求仍可幂等拿到 no-slot baseline。

PATCH overlay 不能过期后永久挂在 slot，也不能静默清掉后按原 Plan 无 patch 执行。lease 前检查或 janitor 发现 `now > gate_valid_until` 时，运行与 REPLAN 相同的 fenced system transition（reason=`PATCH_OVERLAY_EXPIRED`）：CAS 当前 state/slot，设置 replan pending/invalidate-from，cancel 目标及其后 PENDING、revoke LEASED 并 bump token，把匹配的 SELECTING_DIRECTOR claim 转 BASELINE_FALLBACK_PENDING，写一次性 SAFE_RECOVERY/NORMAL_BASELINE override、enqueue replan 和 typed events；terminal slot 保留旧 gate pair 作 provenance。若 worker commit 与 expiry 竞争，commit 只有在事务锁内仍未过期且 token/state 匹配时能赢；expiry 先赢则旧 worker 必败并用同 request claim 走无 slot baseline/recovery，不能把过期 patch 转成 SLOT_SUBSTITUTED。新 Plan 激活后从 committed-through+1 重建 slot，旧 overlay 永不复制。

本例的 revision 时序是明确的：DirectorInput/Plan basis 为 118；Plan activation CAS 成功后把 active pointer 与 `state_revision 118 → 119` 一起提交，所以 CompiledPolicy 期待 119；Slot 18 commit 再把 `119 → 120`；IntentProjector 把 suppression 与 `session_policy_revision 17 → 18` 原子写入，并把 `120 → 121`；随后才构造 GateDecision，所以它期待 121；Gate apply 成功后再把 state revision 增至 122。下一生成请求原子消费 SAFE_RECOVERY override、复制 Gate provenance 后把 `122 → 123`，所以 BaselineRecoveryPolicy 期待 123。普通原始点击只进入事件流，不直接改 control-plane state revision。

### 10.5 DecisionLog

DecisionLog 是 append-only 事件账本，不复制完整 Plan、候选或推荐 item 列表：

```json
{
  "schema_version": "director-decision-log.v1",
  "event_id": "01K4LOG...",
  "event_type": "BATCH_COMMITTED",
  "occurred_at": "2026-08-31T10:00:05Z",
  "operation_id": "01K4OP...",
  "idempotency_key": "sha256:commit...",
  "trace_id": "01K4TRACE...",
  "actor": "COMPOSER",
  "mode": "ENFORCE",
  "director_runtime_epoch": 12,
  "profile_id": "default",
  "feed_session_id": "fs_01K4...",
  "slot_seq": 18,
  "refs": {
    "plan_id": "01K4PLAN...",
    "plan_revision": 7,
    "candidate_set_ref_id": "01K4SERVE...",
    "compiled_policy_id": "01K4POL...",
    "batch_instance_id": "01K4BATCH18..."
  },
  "input_artifact_digest": "sha256:policy...",
  "output_artifact_digest": "sha256:batch-commit...",
  "provenance": {
    "producer": "recommendation.director.composer",
    "code_version": "git:example",
    "schema_versions": ["compiled-policy.v1", "director-decision-log.v1"],
    "provider": null,
    "model": null,
    "prompt_fingerprint": null
  },
  "outcome": "APPLIED",
  "reason_codes": [],
  "experiment": {
    "experiment_id": "director-single-slot-v1",
    "assignment_id": "01K4ASSIGN...",
    "unit_type": "PROFILE_TIME_BLOCK",
    "unit_hash": "sha256:session-unit...",
    "eligible_strategy_actions": ["baseline", "director"],
    "assigned_strategy_action": "director",
    "assignment_probability": 0.5,
    "rollout_stage": "PHASE_2_SINGLE_SLOT",
    "execution_horizon_limit": 1,
    "strategy_action_probability": null
  },
  "detail": {
    "detail_type": "batch_committed.v1",
    "requested_execution_kind": "PLAN_SLOT",
    "committed_execution_kind": "PLAN_SLOT",
    "execution_outcome": "DIRECTOR_COMMITTED",
    "served_count": 10,
    "planned_lane_counts": {"anchor_agent": 5, "adjacent_workflow": 3, "adjacent_feedback": 2},
    "realized_lane_counts": {"anchor_agent": 5, "adjacent_workflow": 3, "adjacent_feedback": 2},
    "degradation_codes": [],
    "duration_ms": 84
  }
}
```

`detail` 是按 `detail_type` 判别的严格联合，`refs` 也按事件类型校验，而不是一个什么都能塞的 JSON blob：

| event family | detail_type | 必填 refs / digest |
|---|---|---|
| `PLANNING_REQUESTED` | `planning_requested.v1` | planning_request_id + input_digest |
| `PLANNING_DEFERRED` | `planning_deferred.v1` | planning_episode_id + terminal deferral reason；无 DirectorInput 时不要求 planning_request_id |
| `PLANNING_FAILED` | `planning_failed.v1` | planning_request_id + input_digest + provider/error provenance |
| `PLAN_PROPOSED` | `plan_proposed.v1` | planning_request_id + input/output digest + model provenance |
| `PLAN_REJECTED` | `proposal_rejected.v1` | planning_request_id + proposal/output digest + validation codes；不得要求不存在的 plan ID |
| `PLAN_ACCEPTED / PLAN_ACTIVATED` | `plan_validation.v1` | plan_id/revision + plan digest；activation 另带 pre/post state revision |
| `EXPERIMENT_ASSIGNED` | `experiment_assigned.v1` | experiment/assignment object + unit hash |
| `SLOT_LEASED / SLOT_LEASE_EXPIRED / SLOT_LEASE_RELEASED / SLOT_REVOKED / SLOT_CANCELED / SLOT_COMMITTED / SLOT_SUBSTITUTED / SLOT_PRESENTED / SLOT_CLOSED` | `slot_transition.v1` | plan/revision/slot + fencing token；commit/substitution/presentation/close 还需 batch |
| `POLICY_COMPILED` | `policy_compiled.v1` | plan/slot + policy/ref/constraint IDs 与 input/output digests |
| `POLICY_EVALUATED_SHADOW` | `shadow_evaluation.v1` | shadow plan + evaluation ID/digest，无 execution lease |
| `SHADOW_EVALUATION_FAILED` | `shadow_failure.v1` | shadow plan + evaluation key + failure code，无 control-plane mutation |
| `BATCH_COMMITTED / FALLBACK_SERVED` | `batch_committed.v1` | batch + discriminator 对应的 compiled/recovery policy ref 或 NONE + candidate ref；聚合 planned/realized mix |
| `BATCH_PRESENTED` | `batch_presented.v1` | batch + presentation ID、coverage counts |
| `BATCH_FALLBACK_PENDING / BATCH_BASELINE_CLAIMED / BATCH_CLOSED / BATCH_FAILED / REQUEST_CLAIM_EXPIRED / PRESENTATION_UNKNOWN` | `batch_lifecycle.v1` | batch + request digest + from/to status + reason code |
| `GATE_DECIDED` | `gate_decided.v1` | gate + plan（可空）+ cursor range |
| `SESSION_INTENT_UPDATED / SESSION_SUPPRESSION_EXPIRED` | `session_projection.v1` | session + pre/post state/policy revisions + source event refs |
| `PROFILE_VIEW_UPDATED` | `profile_view_transition.v1` | profile + pre/post profile revision/digest + upstream generation |
| `OVERRIDE_CONSUMED / OVERRIDE_EXPIRED` | `override_transition.v1` | gate + session/state revisions；consumed 另带 batch |
| `PLAN_SUPERSEDED` | `plan_superseded.v1` | old/new plan refs + canceled/revoked counts |
| `PLAN_DELIVERED / PLAN_COMPLETED / PLAN_EXPIRED / PLAN_FAILED` | `plan_lifecycle.v1` | plan/revision + from/to status + state revisions + close counts |
| `SESSION_CLOSED` | `session_closed.v1` | session + state revisions + close reason/counts |
| `FEEDBACK_CORRECTED / FEEDBACK_RETRACTED` | `feedback_correction.v1` | original feedback event + correction event + cursor effect |
| `REWARD_OBSERVED` | `reward_observed.v1` | batch + assignment + reward window/version |

未使用的 ref 必须省略，不能以大量 null 绕过 variant 检查。每个 detail model 都 `additionalProperties: false`，自由 diagnostic 只允许进入有界、脱敏的 opaque blob ref。

实现时 `detail` 必须是 `Annotated[Union[...], Field(discriminator="detail_type")]`。各 v1 detail 的精确最小字段锁定如下（除 `detail_type` 外均不可换成自由 dict）：

```text
planning_requested.v1 = planning_episode_id, planning_key, trigger_type,
                        reason_codes, accept_until
planning_deferred.v1 = planning_episode_id, desired_context_version,
                       terminal_reason, trigger_refs_digest, deferred_at
planning_failed.v1 = failure_stage, error_code, provider, model(optional),
                     attempt, duration_ms, retryable
plan_proposed.v1 = output_digest, provider, model, prompt_fingerprint,
                   input_tokens, output_tokens, cache_hit, duration_ms, estimated_cost
proposal_rejected.v1 = output_digest, validator_version, validation_codes
plan_validation.v1 = plan_digest, validator_version, pre_state_revision,
                     post_state_revision(optional for ACCEPTED, required for ACTIVATED)
experiment_assigned.v1 = assignment_id, unit_hash, eligible_actions_digest,
                         assigned_action, assignment_probability,
                         rollout_stage, execution_horizon_limit
slot_transition.v1 = from_status, to_status, fencing_token, lease_request_id(optional),
                     reason_codes, batch_instance_id(required for COMMITTED/SUBSTITUTED/PRESENTED/CLOSED)
policy_compiled.v1 = compile_key, policy_digest, constraint_digest,
                     compile_status, repair_codes, duration_ms
shadow_evaluation.v1 = evaluation_key, virtual_policy_digest,
                       virtual_mix, violation_count, duration_ms
shadow_failure.v1 = evaluation_key, failure_stage, error_code, duration_ms
batch_committed.v1 = served_count, planned_lane_counts(optional for fallback),
                     realized_lane_counts, degradation_codes, duration_ms,
                     requested_execution_kind, committed_execution_kind,
                     execution_outcome
batch_presented.v1 = presentation_id, payload_digest, presented_count,
                     batch_count, presentation_status, late_ack
batch_lifecycle.v1 = from_status, to_status, reason_codes, request_id_digest,
                     binding_disposition(optional), execution_fencing_token(optional),
                     presentation_deadline(optional),
                     closed_at(optional)
gate_decided.v1 = gate_key, gate_policy_version, action, strength,
                  cursor_after, cursor_through, event_set_digest, reason_codes
session_projection.v1 = operation, pre_state_revision, post_state_revision,
                        pre_session_policy_revision, post_session_policy_revision,
                        pre_session_view_digest, post_session_view_digest,
                        target_ids, source_event_ids, suppression_expires_at(optional)
profile_view_transition.v1 = projection_version, pre_profile_policy_revision,
                             post_profile_policy_revision, pre_profile_view_digest,
                             post_profile_view_digest, source_profile_generation,
                             source_event_ids, changed_target_ids
override_transition.v1 = action, override_mode, valid_until,
                         pre_state_revision, post_state_revision,
                         max_uses, remaining_uses_after,
                         batch_instance_id(required iff action=CONSUMED)
plan_superseded.v1 = old_plan_digest, new_plan_digest,
                     canceled_count, revoked_count, pre_state_revision, post_state_revision
plan_lifecycle.v1 = from_status, to_status, reason_codes,
                    pre_state_revision, post_state_revision,
                    committed_through_slot_seq(optional), canceled_slot_count,
                    revoked_slot_count, closed_batch_count
session_closed.v1 = close_reason, pre_state_revision, post_state_revision,
                    closed_batch_count, closed_slot_count,
                    completed_plan_count, expired_plan_count,
                    late_feedback_cutoff_at
feedback_correction.v1 = original_feedback_event_id, correction_event_id,
                         operation, cursor_after, cursor_through,
                         affected_gate_decision_ids, reason_code
reward_observed.v1 = assignment_id, reward_window_id, reward_version,
                     metric_values, attribution_status
```

`feedback_correction.v1.operation` 只能是 `CORRECT / RETRACT`，并必须和 event type 一致。它只追加新的事实和后续 reducer/cursor 影响，绝不改写原 feedback、Gate 或 reward 事件；若已关闭窗口，只进入长期学习/修正后的 reward version，不回滚已提交 batch。`batch_lifecycle.v1` 的 `BATCH_FALLBACK_PENDING` 只能是 `SELECTING_DIRECTOR → BASELINE_FALLBACK_PENDING`；`BATCH_BASELINE_CLAIMED` 可为 `BASELINE_FALLBACK_PENDING → BASELINE_SELECTING`（无 slot）、`SELECTING_DIRECTOR → BASELINE_SELECTING`（仍持有有效 slot、准备 whole-baseline substitution）或 `SELECTING_RECOVERY → BASELINE_SELECTING`（recovery reservation 已取消），detail 必须用 reason/binding disposition 区分。`REQUEST_CLAIM_EXPIRED` 只能从四种 selecting/pending 状态进入 FAILED；`PRESENTATION_UNKNOWN` 不改变 committed execution outcome，只冻结该 batch 的方向归因资格。`plan_lifecycle.v1` 的 event type、from/to status 与 reason 组合使用闭合 transition table 校验。detail 中 `reason_codes` 必须与顶层数组逐项相等；使用 singular reason 的 variant 则要求顶层恰为该 singleton。

`session_projection.v1.operation` 只能为 `UPDATE_INTENT / EXPIRE_SUPPRESSION`，并分别与 SESSION_INTENT_UPDATED/SESSION_SUPPRESSION_EXPIRED 一一对应；两者 pre/post state 和 session policy revision 均恰好 +1，source_event_ids 可引用原 suppression 的来源事件但不存 note。`profile_view_transition.v1` 的 pre revision 从 0 开始、初建 pre digest 为 null，后续 revision 恰好 +1；只在 digest 真正变化时写事件。跨 session view 更新可在任何 runtime mode 下发生，日志 mode 记实际当前 mode，不能伪装成某个 FeedSession 的反馈。

`batch_lifecycle.v1.binding_disposition` 只允许 `CLEAR_TO_NO_SLOT / RETAIN_VALID_SLOT / NONE`：FALLBACK_PENDING 必须 CLEAR_TO_NO_SLOT；从 pending claim baseline 必须 CLEAR_TO_NO_SLOT；直接 execution substitution 必须 RETAIN_VALID_SLOT 且带 execution fencing token；其它 batch lifecycle 必须 NONE。该字段与 Batch 的 plan/slot/reservation refs 做 equality/emptiness validator。

`slot_transition.v1.lease_request_id` 对 SLOT_LEASED、SLOT_LEASE_EXPIRED、SLOT_LEASE_RELEASED、SLOT_COMMITTED、SLOT_SUBSTITUTED、SLOT_REVOKED 必填并等于该 transition 获取/消费/撤销的 request claim；对 CANCELED/PRESENTED/CLOSED 必须省略。目标为 PENDING 的两种回边由 event type 区分：SLOT_LEASE_EXPIRED 仅 janitor 超时，SLOT_LEASE_RELEASED 仅执行失败后主动终结原 request；两者都是 LEASED→PENDING 且 fencing token 已递增。初建 PENDING 无 transition event。`from_status` 只允许 12.2 状态图中的边；presentation/close 使用 committed batch ref，不能再携带一个看似 active 的 lease。

顶层枚举锁定为：`actor = PLANNER | VALIDATOR | PLAN_STORE | EXPERIMENT_ASSIGNER | COMPILER | COMPOSER | CLIENT_ACK | GATE | PROJECTOR | PROFILE_REDUCER | LEARNER | SYSTEM`；`mode = BASELINE | SHADOW | ENFORCE | RECOVERY`；`outcome = APPLIED | OBSERVED | REJECTED | STALE | FALLBACK | FAILED`。SHADOW/ENFORCE/RECOVERY 事件必须带创建 artifact 时的 `director_runtime_epoch`；Director-aware baseline fallback 也带触发 epoch，纯 legacy baseline 可为空。PROFILE_VIEW_UPDATED 是跨 FeedSession 的 profile 事件：其 `feed_session_id` 必须为 null，profile_id 必填；session projection 事件则两个 ID 均必填。artifact digest 对对应 strict typed object 的无自引用 JCS 投影求 hash；不存在输入/输出的 variant 必须省略字段，不能填任意 placeholder。

逐事件幂等 key 投影也锁定：planning request=`planning_key`；planning failure=`planning_request_id+attempt+failure_stage+error_code`；proposal=`planning_request_id+output_digest`；validation=`planning_request_id+output_digest+validator_version+outcome`；activation=`plan_id+revision+pre_state_revision`；assignment=`assignment_id`；slot transition=`plan_id+slot_seq+to_status+fencing_token`；policy=`compile_key`；shadow success/failure=`evaluation_key+outcome`；batch commit=`batch_instance_id+request_id+execution_fencing_token`；presentation=`presentation_id+payload_digest`；batch lifecycle=`batch_instance_id+to_status+JCS(reason_codes)+request_id_digest`；Gate=`gate_key`；override=`gate_decision_id+action+post_state_revision`；supersede=`old_plan_id+new_plan_id+pre_state_revision`；plan lifecycle=`plan_id+revision+to_status+pre_state_revision`；session close=`feed_session_id+post_state_revision+close_reason`；feedback correction=`original_feedback_event_id+correction_event_id+operation`；reward=`batch_instance_id+assignment_id+reward_window_id+reward_version`。

PLANNING_DEFERRED 的 key 是 `planning_episode_id + desired_context_version + terminal_reason`；无 Input 时 refs 只带 episode ID。`terminal_reason` 闭合为 `BUDGET_EXHAUSTED / HIGH_CHURN / SESSION_CLOSED / RUNTIME_DISABLED`，它不是 provider 错误，不伪造 PLANNING_FAILED。Projection 事件幂等 key 分别是 `feed_session_id + post_session_policy_revision + operation` 与 `profile_id + post_profile_policy_revision + projection_version`；它们的 pre/post digest、revision 和来源事件集合必须与同事务写入的状态逐值相等。SLOT_LEASE_RELEASED 的 key 在 slot transition 通式中额外带 `lease_request_id + reason_codes`，避免同一 fencing token 的失败释放与超时释放混淆。

要求：

- v1 event type 闭合枚举为 `PLANNING_REQUESTED / PLANNING_DEFERRED / PLANNING_FAILED / PLAN_PROPOSED / PLAN_ACCEPTED / PLAN_REJECTED / PLAN_ACTIVATED / EXPERIMENT_ASSIGNED / POLICY_COMPILED / POLICY_EVALUATED_SHADOW / SHADOW_EVALUATION_FAILED / SLOT_LEASED / SLOT_LEASE_EXPIRED / SLOT_LEASE_RELEASED / SLOT_REVOKED / SLOT_CANCELED / SLOT_COMMITTED / SLOT_SUBSTITUTED / SLOT_PRESENTED / SLOT_CLOSED / BATCH_COMMITTED / BATCH_PRESENTED / BATCH_FALLBACK_PENDING / BATCH_BASELINE_CLAIMED / BATCH_CLOSED / BATCH_FAILED / REQUEST_CLAIM_EXPIRED / PRESENTATION_UNKNOWN / GATE_DECIDED / SESSION_INTENT_UPDATED / SESSION_SUPPRESSION_EXPIRED / PROFILE_VIEW_UPDATED / OVERRIDE_CONSUMED / OVERRIDE_EXPIRED / PLAN_SUPERSEDED / PLAN_DELIVERED / PLAN_COMPLETED / PLAN_EXPIRED / PLAN_FAILED / SESSION_CLOSED / FALLBACK_SERVED / FEEDBACK_CORRECTED / FEEDBACK_RETRACTED / REWARD_OBSERVED`；增加类型必须升级 schema/registry，不能在 v1 用自由字符串旁路；
- `(event_type, idempotency_key)` 唯一；状态生效的日志与对应状态写入同一事务；
- event type 必须唯一映射到上表的 detail variant；detail、顶层、`refs`、`provenance`、`experiment` 中重复出现的 ID、revision、digest、reason、provider/model/prompt、assignment/presentation 字段必须逐值相等。任何 mismatch 都拒绝整笔状态事务，不能以某一层为“近似真相”；
- `experiment.assignment_probability` 是 assignment 时记录的真实实验分流概率；未随机分流时为 1，未进入实验时整个 experiment 为 null；`EXPERIMENT_ASSIGNED` 必须和 assignment 状态同事务；
- `experiment.strategy_action_probability` 只在未来 bandit 真正随机选择策略臂时填写，不能事后估算；
- item 与 server rank 已在 recommendation rows，actual rendered position 在 presentation items，均通过 `batch_instance_id` 关联；日志只保留聚合，避免重复敏感明细；
- reward 作为后续事件关联，不回写覆盖原 decision；
- 记录 fallback、repair、模型、prompt fingerprint 和策略版本，支持控制决策/状态 replay；仅靠 digest 不声称能在 exact members 清理后重选同一批 item；
- Planner 事件记录 provider/model、input/output token、cache hit、duration 和估算成本；
- 原始 prompt、完整画像、标题、URL、候选 ID 列表和自由文本 feedback note 不进入分析日志；
- 本地明细默认保留 90 天，长期只保留脱敏聚合。

### 10.6 语义状态与运行账本

不可变 Plan 的执行控制面只允许下列三个职责单一的小型可变记录；Batch、presentation、feedback queue 等属于后文独立运行账本，不塞回 Plan JSON：

```text
DirectorSessionState
  profile_id, feed_session_id, state_revision,
  active_plan_id/revision, next_slot_seq, committed_through_slot_seq,
  current_batch_instance_id,
  gate_feedback_cursor, session_policy_revision, session_view_digest,
  suppression_ref_id, suppression_digest, suppression_valid_until,
  applied_gate_decision_id, applied_gate_key,
  next_batch_override_mode, next_batch_override_gate_decision_id,
  next_batch_override_valid_until, next_batch_override_remaining_uses,
  replan_pending_version, invalidate_from_slot_seq,
  director_rollout_exhausted

PlanSlotState
  plan_id, slot_seq, status, lease_owner, lease_expires_at,
  lease_request_id, fencing_token, slot_revision,
  applied_gate_decision_id, patch_overlay_digest,
  stay_patch_count,
  execution_outcome, substitute_reason, cancel_reason, revoke_reason,
  committed_batch_id, presented_at, closed_at

DirectorSessionOperational
  profile_id, feed_session_id, operational_revision,
  plan_calls_used, provider_calls_total, context_churn_retries_used,
  budget_window_started_at,
  last_replan_requested_at
```

`recommendation_director_state.state_revision` 是 session 语义 control plane 唯一的 optimistic-lock 版本；`session_policy_revision + session_view_digest` 也只在这一行保存，表示 session 意图语义的单调版本与内容 hash。`session_view_digest` 对移除自身后的 `session_policy_revision + normalized directives + density/exploration controls + suppression_ref/digest/valid_until` canonical projection 求 hash；recent batch summaries、active plan/cursor 和 raw event 不属于该投影，分别由 input_digest/其它 basis 字段覆盖。raw feedback cursor 属于事件流；只有 Gate 成功消费后的 `gate_feedback_cursor` 进入此状态。`PlanSlotState.slot_revision/fencing_token` 独立保护 slot lease，单纯 acquire/timeout lease 不递增 session state revision。

语义字段闭合集合为：active plan/cursor/current batch、Gate cursor、session intent/suppression、applied Gate/override、replan invalidation 和 rollout exhausted。只有这些字段改变才 CAS `state_revision + 1`；同一事务改多个字段也只加 1。`DirectorSessionOperational` 的 cooldown 和调用预算只用自己的 `operational_revision` 或 SQL 原子计数/条件更新；FeedSession activity 用 `activity_revision`。它们都不递增 Director state revision，也不进入 Plan/Policy stale basis。预算扣减与 planning-request claim 同事务；activity touch 与 FeedSession `OPEN` 条件更新，cleanup CAS 失败时必须重读，避免关掉刚活跃的 session。Candidate/slot lease、Batch selecting claim、presentation/feedback queue 也各用自己的 key/revision。这样普通 heartbeat、预算记账或 lease timeout 不会让异步 Plan 无意义 stale。

Profile 侧由新增的持久化 `RecommendationProfileView` reducer 拥有 `profile_policy_revision + profile_view_digest`：它从 Soul/profile 的可用于推荐字段确定性投影，语义 digest 改变时递增，并在同一事务追加 `PROFILE_VIEW_UPDATED(profile_view_transition.v1)`；不复用或覆盖 `MemoryManager.set_profile_change_callback()`。每一类可变对象都带自己上段指定的 revision/fencing/CAS；LLM 永远不能写这些对象。

该 reducer 故意不把每次 Soul storage generation 直接等同于 Director policy revision。v1 stable projection 只纳入：用户显式长期约束；在本 session 前已经稳定存在的 active interest/style；或由至少两个不同事件 ID/来源确认后跨过 registry 阈值的新增偏好。现有单次 recommendation click 仍可立即更新 Soul，也进入当前 `session_summary`，但若只形成一次新证据，不改变 Director profile digest；第二个独立证据使 stable projection 变化时，才产生 `PROFILE_POLICY_CHANGED`。Reducer 保存 `source_profile_generation` 以便审计“上游变了但 Director view 未变”，阈值与投影版本必须可 replay。

#### 10.6.1 状态条件校验

`state_revision` 每个成功 control-plane 事务恰好 `+1`，不能跳号或回退；`session_policy_revision` 只在 semantic overlay 变化时 `+1`，且该事务也必须提升 state revision。无 active pointer 时 `next_slot_seq` 为空，历史 `committed_through_slot_seq` 可保留作下一规划起点；ACTIVE plan 要求 `next_slot_seq > committed_through_slot_seq`；DELIVERED plan 要求 next 为空且 committed-through 等于最后 slot。`replan_pending_version` 与 `invalidate_from_slot_seq` 必须同时为空或同时存在，激活对应更新计划后原子清空。

四个 `next_batch_override_*` 字段必须全空或全有；存在时 mode 只能 NORMAL_BASELINE/SAFE_RECOVERY、remaining uses 必须为 1、decision 必须是当前 session 已 APPLIED 且未过期的 REPLAN/FALLBACK Gate。下一次合法生成请求在取得 generation mutex、创建 Batch/可选 reservation 的 claim 事务中，把 gate ID/mode/valid-until 复制到 Batch 的 immutable consumed-override provenance，再将 remaining uses `1 → 0` 并清空四个 state 字段、`state_revision +1`，同事务写 `OVERRIDE_CONSUMED`。selection 失败也由该 Batch terminal record 保留已消费事实，不能让另一请求重复消费。TTL 到期由 fenced janitor清空、state revision +1 并写 `OVERRIDE_EXPIRED`，不选择内容。

| slot status | 必填 | 必须为空/禁止 |
|---|---|---|
| `PENDING` | plan/slot、slot_revision、fencing_token | lease owner/request/expiry、batch、execution/reason、presentation/close time |
| `LEASED` | owner、future expiry、`lease_request_id`、fencing token | committed batch、execution/reason、presentation/close time |
| `COMMITTED` | committed_batch_id + `execution_outcome=DIRECTOR_COMMITTED` | active lease owner/request/expiry；执行归属不可改 |
| `SUBSTITUTED` | committed baseline batch + `execution_outcome=BASELINE_SUBSTITUTED` + substitute reason | active lease owner/request/expiry；不得计 adherence |
| `PRESENTED` | committed_batch_id + presented_at + immutable execution_outcome | active lease owner/request/expiry |
| `CLOSED` | committed_batch_id + closed_at + immutable execution_outcome | active lease owner/request/expiry |
| `CANCELED` | cancel reason | lease owner/request/expiry、batch、execution、presentation/close time |
| `REVOKED` | revoke reason + 已递增 fencing token | lease owner/request/expiry、batch、execution、presentation/close time |

`LEASED.lease_request_id` 必须等于同 FeedSession 中唯一 `BatchInstance(status=SELECTING_DIRECTOR)` 的 request claim；lease acquire 与 SELECTING_DIRECTOR claim 同事务，final commit/timeout/revoke 时清除所有 active lease 字段。若要审计已提交 request，从 immutable BatchInstance 与 DecisionLog 关联，不能把 `lease_request_id` 留在 terminal slot 冒充活跃租约。

Gate overlay 的 decision ID 与 digest 必须成对出现。它们在 PENDING/LEASED 可为空或成对存在；一旦 slot 进入任一 terminal/execution 状态，保留当时 pair 作为不可变 provenance（从未应用则仍成对为空），但永远不能据此复活或重新 lease。`execution_outcome` 在首次 commit 时写入并在 status 变为 PRESENTED/CLOSED 后仍保留，避免丢失 substitution provenance。fencing token、slot revision、session state revision 和各 cursor 均单调；CANCELED/REVOKED 不可复活，COMMITTED/SUBSTITUTED 以后只允许 presentation/close 生命周期推进，不能改变 execution outcome、plan、policy 或 batch 归属。所有这些规则同时实现为 Pydantic validator、数据库 CHECK/UNIQUE（SQLite 能表达的部分）和事务前置断言。

### 10.7 机械验证附录

实现不得把以下细节留给各模块自行解释：

- 时间在 SQLite 以 UTC epoch milliseconds INTEGER 保存和比较；API/canonical JSON 使用 UTC RFC 3339：毫秒为 0 时固定省略小数（`...SSZ`），否则固定三位（`...SS.sssZ`）。`created/requested/committed <= expires/accept/feedback/valid_until`；lease/reservation/ref/policy 的 expires 必须严格晚于 created/acquired，所有 TTL 比较使用服务器时钟。客户端时间只作附属事实；
- strict integer 拒绝 JSON bool/float coercion。ID 使用各 schema 声明的 ULID/opaque-ref pattern，digest 使用 `^sha256:[0-9a-f]{64}$`；`lane_key` 使用 `^[a-z][a-z0-9_]{0,63}$`。本文 `01K4...`、`sha256:plan...` 等只是可读占位符，不是会通过生产 validator 的 golden fixture；测试 fixture 必须使用真实长度值；
- set-like arrays 必须唯一，但不一律按字符串排序：topic/catalog/anchor arrays 使用 frozen taxonomy ordinal，style 使用 registry/偏好优先级，reason/evidence 使用 registry predicate priority，blocked/enabled sets 才按 canonical ID 升序；这些 field-specific order 属于 schema version。`slots`、PlanLanes、`fallback_lane_keys`、compiler trace 和 recent batches 另有显式语义顺序。服务器在计算 artifact digest 前完成对应 normalization，之后 JCS 保留数组顺序；Validator 不得在 hash 后重排。本文正式 Input/Plan/Policy 展示的就是该版本 canonical order；
- v1 Proposal/Plan 恰好 3 slots；每 slot `1..3` PlanLanes；每个 PlanLane topic IDs 默认 `2..8`，只有服务器 capacity proof 标记的 single-topic exception 可为 1；每 slot rationale `1..3`、evidence refs `1..4`。style `prefer/avoid` 各自唯一且交集为空；同一 topic 不得在同 slot 的两个 PlanLane 重复，否则候选归属不唯一；
- counts、cursor、revision、slot seq、fencing token 均为非负整数，其中 revision/slot seq/token 在创建执行对象后为正；confidence/quality/assignment probability 在 `[0,1]`，随机 assignment probability 必须 `>0`，`quality_p50 <= quality_p90`；min/target/max 与 requested limit 使用 10.3/15.1 的范围和缩放规则；
- CandidateSetRef 与 DirectorInput 都限制 prompt-visible `capability_cells` 最多 24 个，排序为 `servable_count desc → quality bucket/score desc → topic/style/duration/source ID asc`。`0 <= len(cells) <= capability_cell_count`，且 `capability_cells_truncated == (len(cells) < capability_cell_count)`；false 时必须完整相等。Input 的 count、truncated、visible cells 必须与其 planning ref 的投影一致；
- topic marginals 的每个 key 必须属于 frozen catalog、value 非负，`sum(topic_marginals)+unknown_topic_count==member_count`；`topic_counts_complete=true` 时 Input 也必须满足同一等式。所有 map 禁止重复 canonical key 或 unknown alias；
- 所有 nullable union 使用“整组为空或整组存在”的 condition validator；禁止依赖 JSON Schema `default` 补权威字段。Pydantic、JSON Schema、SQLite CHECK 能表达的约束由一组 golden/negative fixtures 共同生成或逐项对照，事务前置断言只补跨表/时钟/图约束。

## 11. FeedSession 与生成请求合同

Director 不能从裸 `serve(limit=10)` 猜测用户在做什么。客户端必须显式创建或恢复 FeedSession，并在每次生成请求携带：

```text
request_id
api_contract_version
feed_session_id
client_instance_id
surface
source_platform_scope
feed_mode
batch_intent
requested_limit
previous_batch_instance_id (optional)
restore_batch_instance_id (RESTORE only)
```

### 11.1 `batch_intent` 语义

| intent | 行为 | 是否消费 plan slot |
|--------|------|--------------------|
| `ADVANCE_PLAN` | 关闭当前可见栈内全部 open batch feedback window，只 lease state 指向的 exact next PENDING slot | 是 |
| `APPEND_PLAN` | 页面追加下一段，语义等同 exact-next advance；旧批保持可读/可见且反馈窗口只保留到自身 deadline | 是 |
| `STAY_SAME_DIRECTION` | 沿当前 recipe/lane 生成替换批，不把 reshuffle 当负反馈 | 否；生成关联当前 slot 的 ephemeral patch batch |
| `BASELINE_TOPUP` | 当前 GET 首屏补货 | 否；强制 baseline |
| `PREFETCH` | 仅准备只读能力快照 | 否；不得写 recommendation 或 shown |
| `RESTORE` | 恢复已提交批次 | 否；只读 |
| `CLI_ONE_SHOT` | CLI/OpenClaw 旧调用 | 否；v1 默认 baseline |

现有 reshuffle 在没有新参数时映射到 baseline 兼容路径；它永远是 satisfaction-neutral，不生成 Director STAY_PATCH。只有采用新 FeedSession 请求合同的 Director-aware UI 才能发送 `STAY_SAME_DIRECTION`，并应显式提供“继续这条线 / 换个方向 / 轻松一点 / 暂时不看 X”，不要让系统从 reshuffle 或 skip 猜用户意图。

`previous_batch_instance_id` 按 intent 条件校验：STAY 必填；非首个 ADVANCE/APPEND 在 session 已有 current batch 时也必填；首个 ADVANCE 才可为空。服务器必须验证 previous batch 属于同 FeedSession/client、是 state 指向的最新可操作 batch。ADVANCE 在 request-claim/slot-lease 事务中关闭该 session 当前可见栈内所有 open COMMITTED/PRESENTED batches 及其可归因窗口；APPEND 不关闭旧批而推进语义 cursor。RESTORE 使用独立 `restore_batch_instance_id`，不能借 previous 字段生成内容。

Director-aware v1 的 `requested_limit` 是 strict integer 且范围 `1..20`；RESTORE 忽略生成 limit 并要求它省略。越界生成请求不创建 Director lease/flow，可按 endpoint 合同显式返回 unsupported 或走带 reason 的 baseline，不能截断后假装相同 request。

“保留可见”是客户端展示语义，不表示无限保留控制权。每个 batch commit 时冻结 `feedback_accept_until = min(committed_at + batch_feedback_window_seconds, FeedSession.expires_at, Plan.expires_at if linked)`；APPEND 留下的旧批只在该时间前可驱动当前 Gate，deadline 到达后 janitor 将其 CLOSED。CLOSED batch 仍可 RESTORE/显示，符合 late-ack 规则的行为仍可进入实验 reward/长期学习，但不回溯更改当前方向。

STAY 只允许 origin root batch 是同 session 当前未被 invalidated 的 `DIRECTOR_COMMITTED` batch，plan 仍 ACTIVE/DELIVERED，且 previous batch 是该 root 的最新 patch 链尾。初始 request claim 事务对 PlanSlotState 的 `stay_patch_count` 做 CAS `+1`，并以 `(origin_batch_instance_id, patch_sequence)` UNIQUE；两个 request 不可能都拿到 sequence 1。达到 `max_stay_patch_batches_per_slot` 后返回 `STAY_LIMIT_REACHED` 并建议 ADVANCE，不静默 baseline、不自动消费下一 slot。候选排除 root 与所有已提交 patch rows。

Director-aware `request_id` 是客户端生成的全局唯一 ULID；同一个 ID 的网络重试必须返回同一个 in-progress/terminal batch。服务器在第一次 claim 时冻结 `request-fingerprint.v1`，其 canonical 投影精确包含 `api_contract_version + feed_session_id + client_instance_id + surface + source_platform_scope + feed_mode + batch_intent + canonical(requested_limit|null) + canonical(previous_batch_instance_id|null) + canonical(restore_batch_instance_id|null) + resolved_experiment_assignment_id|null + execution_horizon_limit|null`。重试必须逐字段重算并等于已存 digest；任一行为字段不同都返回 `REQUEST_ID_REUSE_CONFLICT`，不关闭 batch、不改 patch sequence、不消费 slot。Plan pointer、pool generation 等 live execution basis 不进入客户端 fingerprint，而由既有 request claim/lease artifact 冻结；因此同 request 不能因重试时 live state 变化而重绑到新 slot。旧 GET/CLI 没有 client request id，只分配 server-generated `operation_id` 便于追踪，不承诺跨网络重试去重；要获得幂等保证必须使用新版本化 POST/endpoint 合同。

API 合同中的 `source_platform_scope = "all"` 是 Director 的 canonical 值；进入当前 engine/storage 前必须在一个边界适配器中翻译为 `source_platform = ""`。绝不能把字面量 `"all"` 传给现有 `normalize_source_platform()` 后作为过滤条件，否则会过滤掉全部候选。显式平台值才映射为对应现有 token。

### 11.2 Planner 输入禁止项

无论 feed mode 如何，DirectorInput 均禁止：

- 完整候选数组、内容 ID、标题、正文、弹幕、评论、封面或 URL；
- Cookie、账号、凭证、UP 主 ID；
- 原始长期事件流水、完整画像或未归一化 feedback note；
- 隐式推断出的敏感属性；
- 未经截断、provenance 标记和 data delimiter 包裹的外部文本。

## 12. 计划、slot 与 batch 状态机

### 12.1 Proposal 与 PlanEnvelope

```text
DirectorProposal:  RECEIVED → ACCEPTED
                           └→ REJECTED / STALE_REJECTED

PlanEnvelope:      VALIDATED → ACTIVE → DELIVERED → COMPLETED
                                 │          ├─ TTL → EXPIRED
                                 │          └─ newer plan → SUPERSEDED
                                 ├─ TTL → EXPIRED
                                 ├─ rollout slot limit → COMPLETED
                                 ├─ newer plan → SUPERSEDED
                                 └─ invariant → FAILED
                   history is append-only

ShadowEnvelope:    SHADOW_VALIDATED → SHADOW_EVALUATED / FAILED
                   (never ACTIVE; never owns PlanSlotState)
```

正式 `DirectorPlan` JSON 本身不可变。状态属于 PlanEnvelope；replan 创建新 plan，并 cancel 旧计划的 PENDING slot、revoke 尚未提交的 LEASED slot，绝不改写已有执行结果。

最后一个 slot COMMITTED 或 SUBSTITUTED 时，同一事务把 PlanEnvelope 从 ACTIVE 置为 `DELIVERED`，但仍保留 active pointer，不立即触发下一 horizon。`DELIVERED → COMPLETED` 只在用户对最后批发起下一次 ADVANCE/APPEND 且服务器先处理完已到达 feedback、session 显式关闭，或 session/plan feedback window 到期时发生。完成事务关闭该 Plan 仍 open 的 batches 及对应 slot 聚合窗口，清空 active pointer/next slot、保留历史 committed-through、递增 state revision；若用户仍在请求内容，再以 `HORIZON_EXHAUSTED` enqueue 新规划，当前请求不等待 Planner而走 baseline/recovery。若下一次 ADVANCE 前仍无 presentation ack，先标 `PRESENTATION_UNKNOWN`，不能臆造最后批 reward。

Gate 可因 DELIVERED Plan 最后一批的已呈现强反馈触发 replan；新 Plan 激活时以同一 fenced 事务执行 `DELIVERED → SUPERSEDED`，关闭旧 Plan 尚 open 的控制反馈窗口。原先的 `DELIVERED → COMPLETED` 条件只适用于没有被 supersede 的 Plan。

唯一的 ACTIVE→COMPLETED 直达特例是 assignment 固化的 `execution_horizon_limit` 已达到：同事务 cancel 超出 rollout 上限的剩余 PENDING slots，reason=`ROLLOUT_SLOT_LIMIT`，关闭本次已提交 batch/slot 的控制反馈窗口，清 pointer并置 rollout exhausted；此时不要求 committed-through 等于原三-slot Plan 的最后 slot，也不把 canceled slots 计为 delivered。Phase 2 若需要效果统计，presentation/behavior 仍按 CLOSED late-event 规则进入 reward，不重新打开 Gate。

`ACTIVE → FAILED` 只用于服务器检测到无法继续信任该已激活 Plan 的内部 invariant/code-data mismatch，不用于普通缺货或 provider timeout。它与 EXPIRED 使用同一 fenced termination：close open batches、cancel PENDING、revoke LEASED 并 bump token；匹配且仍可响应的 SELECTING_DIRECTOR claim 转 BASELINE_FALLBACK_PENDING，只有 request/session 已过期才 FAILED；随后清 active pointer/override、递增 state revision，同事务写逐对象事件与 `PLAN_FAILED(plan_lifecycle.v1)`。session 仍 open 时 enqueue RECOVERY replan。不得只把 Plan 行改 FAILED 留下可提交 lease。

### 12.2 PlanSlot

```text
PENDING ── CAS lease ─→ LEASED ── fenced Director commit ─→ COMMITTED ─┐
   ▲                      ├─ whole-baseline commit ─────→ SUBSTITUTED ─┼─ ack → PRESENTED → CLOSED
   │                      │                                           └──── close/no ack ───→ CLOSED
   └──── lease timeout/release ─┤                                     │
   └──── replan ────────→ CANCELED                                    │
                          └─ replan/hard conflict ──────→ REVOKED ─────┘ (no presentation)
```

- `LEASED` 有 owner、expiry 和单调 fencing token；进程内 `_serve_lock` 不能替代它；
- `COMMITTED` 表示 recommendation rows、pool shown、batch、slot cursor 和 audit 已在同一事务提交；从此不可重写；
- `SUBSTITUTED` 表示该 ADVANCE 的 Director 执行整体失败、同一时间线位置由 baseline batch 替代；它是不可改写的执行结果、推进 cursor，但不算计划履约，后续仍可随关联 batch 进入 PRESENTED/CLOSED 生命周期；
- `PRESENTED` 表示客户端确认卡片实际渲染或达到定义的曝光条件；只有它能进入曝光效果归因；
- `CLOSED` 表示关联 batch 的控制反馈窗口已因用户推进、deadline、Plan/session 结束而全部关闭；它不要求先 PRESENTED，且必须保留 immutable `execution_outcome`；
- `CANCELED` 只允许从 `PENDING` 进入；`REVOKED` 只允许从 `LEASED` 进入。
- lease timeout 只能由 janitor 对 `LEASED + matching lease_request_id/fencing_token + expired_at` 做 CAS：slot 清 owner/request/expiry、fencing token `+1` 后回 PENDING；同事务把仍匹配的 SELECTING_DIRECTOR claim 置 FAILED(reason=`REQUEST_CLAIM_EXPIRED`)并写 `SLOT_LEASE_EXPIRED + REQUEST_CLAIM_EXPIRED`。若 batch 已转 fallback pending、slot 已 revoked 或 token 不同，janitor 不得复位 slot；
- 执行和整批 baseline 都未成功、但 lease 仍有效且请求要终结时，短事务执行 `LEASED → PENDING`、清 lease 字段、token `+1`，把 Batch/generation claim 置 FAILED，并写 `SLOT_LEASE_RELEASED(reason=EXECUTION_FAILED|AUDIT_FAILED) + BATCH_FAILED`；它不推进 cursor。已 revoke/cancel 的 slot 不允许 release，事务提交失败则交给 lease timeout janitor，不作无日志内存解锁；
- 新 plan 激活与旧 PENDING cancel、旧 LEASED revoke 必须在同一事务完成；revoke 同时递增 fencing token 并清空 owner/expiry，因此任何持有旧 token 的 worker 在最终提交时必败。
- 若旧 worker 的 fenced commit 与 replan 竞争，以数据库串行化顺序为准：commit 先赢会递增 state revision，因此基于旧 revision 的新 Plan activation 必须 `STALE_REJECTED`，再用新 basis 规划并从已提交 slot 之后开始；replan activation 先赢则在同事务 revoke lease，旧 commit 因 status/token/state revision 不匹配而整批放弃。

### 12.3 BatchInstance

```text
SELECTING_DIRECTOR ─────────────────────────────→ COMMITTED → PRESENTED → CLOSED
        ├→ BASELINE_SELECTING (valid-slot substitution) ───┘      │
        ├→ BASELINE_FALLBACK_PENDING → BASELINE_SELECTING ─┘      │
        └→ FAILED                         └→ FAILED                └→ CLOSED(no ack)

BASELINE_SELECTING (normal baseline entry) ─────→ COMMITTED / FAILED
SELECTING_RECOVERY ─────────────────────────────→ COMMITTED
        └→ BASELINE_SELECTING (recovery failed) → COMMITTED / FAILED
```

一个 PlanSlot 通常对应一个 Director BatchInstance；`STAY_SAME_DIRECTION` 可产生关联原 slot 的 ephemeral patch batch，但不会篡改原 slot。Director-aware baseline batch 的 `plan_id` 为空，仍必须有 batch ID 和 client request 幂等记录；legacy baseline 只有 server operation id，不承诺重试幂等。

`BASELINE_FALLBACK_PENDING` 是保留原 request/batch identity 的可接管终态前状态：它没有 Director execution lease，旧 tentative rows 已丢弃，但尚未承诺响应。原 owner、相同 request 的重试或 janitor worker 可通过 batch fencing CAS 将其变为 BASELINE_SELECTING；只有 baseline commit 后才成为 COMMITTED，pending/selecting 超时才 FAILED。不能为了 cleanup 先写 FAILED 又声称同 request 会返回 baseline。

失败规则：

- Director commit 前失败：若随后 baseline 也未成功提交，按 `SLOT_LEASE_RELEASED` 事务释放仍有效的 lease 且不推进 slot；若 baseline 成功，则在其事务中把 leased slot 标为 SUBSTITUTED 并推进 cursor；
- commit 事务失败：整批失败，不标 shown，不推进 cursor；
- commit 后 HTTP 响应丢失：批次仍是 `COMMITTED`，相同 request_id 只读返回它；不能再次选片；
- 长时间无 presentation ack：批次保持 committed 但标 `PRESENTATION_UNKNOWN`，不能当曝光或负反馈；
- 恢复旧列表：只读历史 BatchInstance，不生成新批次。

Presentation ack 至少包含 `presentation_id + batch_instance_id + presented_items[{recommendation_id, rendered_position}] + presented_at`。服务端 response 中每条 row 已有从 1 开始的 immutable `server_rank_position`；v1 客户端可省略部分 row，但呈现的相对次序必须与 server rank 一致，`rendered_position` 必须从 1 连续递增且在 ack 内唯一。同一 `presentation_id` 幂等；Batch 只要有一条实际呈现就可进入 `PRESENTED`，另以 `presentation_status = PARTIAL | COMPLETE | UNKNOWN` 记录覆盖度。从行级行为推导的方向反馈只能归因到 ack 中实际呈现的 recommendation rows/positions；用户直接发出的结构化 session 指令（如“不看 X”）是独立一等输入，可立即生效，不要求先证明某张卡已呈现，也不能伪装成某 PlanLane 的效果归因。

服务器必须校验 ack 的 authenticated/local client identity 与 FeedSession/client_instance 匹配，Batch 已有不可变 committed execution outcome，且每个 recommendation_id 去重后都真实属于该 batch；数量不得超过 batch rows/payload 上限，rendered positions 与 server-rank relative order 必须一致。客户端 `presented_at` 单独保存，只接受在 batch committed_at 之后且不超过 server now 允许时钟偏差的值，另存权威 `received_at`，归因窗口以 server 时间为准。跨 batch ID、未知 row、重复/跳号 position、同 presentation ID 不同 payload 一律冲突拒绝。click/save 等事件桥同样只信 recommendation_id，由服务器 join 出 batch/plan/slot/PlanLane/server+rendered position；客户端传来的 batch/lane 只能作一致性检查，不能作归因真相。

在 `presentation_late_ack_seconds` 内，COMMITTED、PRESENTED 或 CLOSED batch 都可追加不可变 ack；CLOSED 不重开，只更新 presentation 聚合/行级事实并追加 late-ack event。若 ack 与 horizon close 竞争，以事务顺序为准：ack 先提交可进入当前 Gate，close 先提交则 late ack 只进入实验 reward/长期学习，不回溯打断已完成或新计划；超出窗口只记 operational audit，不进入归因。必须有 ack-vs-close 并发测试。

#### 12.3.1 Close-set 与原子关闭合同

关闭不是只改一个 pointer。单个 fenced close 事务先冻结 close-set，再同步写 Batch、PlanSlot、DirectorSessionState 与 typed DecisionLog：

- `ADVANCE_PLAN`：close-set 是该 FeedSession 当前所有 `COMMITTED / PRESENTED` 且 `closed_at IS NULL` 的 Director-aware batches（含 APPEND 留下的旧批和 STAY patch）；然后才 claim request 并 lease exact-next slot。旧 rows 仍可读，但不再驱动 Gate；
- `STAY_SAME_DIRECTION`：只关闭 previous patch-chain tail 的 batch window，再创建同 origin slot 的新 patch reservation；其它 APPEND 可见批按自身 deadline；
- `APPEND_PLAN`：不提前关闭旧批；每个旧批由 frozen `feedback_accept_until` 独立到期；
- Plan `COMPLETED / EXPIRED / SUPERSEDED`：关闭该 Plan/root slots 关联的全部 open batches；session close/TTL 则关闭该 FeedSession 的全部 open batches，包括无 plan 的 recovery；
- batch deadline janitor：只关闭到期 batch；若它是某 slot/root 的最后一个 open primary/patch batch，同事务把该 PlanSlot 从 COMMITTED/SUBSTITUTED/PRESENTED 置 CLOSED。只要仍有一个关联 batch window open，slot 保持原 execution/presentation status；
- selecting 状态不属于 open feedback batch：session 明确关闭/过期时置 FAILED；Plan/replan/runtime invalidation 且 session/request 仍可响应时，SELECTING_DIRECTOR 先转 BASELINE_FALLBACK_PENDING，而不是丢掉幂等请求。PENDING/LEASED 按 cancel/revoke 规则终结。每个 batch/slot 状态变化各写一个闭合 event，Plan/session lifecycle event 再汇总 counts，均与状态同事务。

`feedback_accept_until`、`closed_at`、`close_reason` 一经写入不可延长或清空。关闭不删除 recommendation rows、presentation rows 或 `execution_outcome`；APPEND 的“保留可见”因此与服务端反馈窗口有明确分界。

## 13. Scope、推进与现有 API 迁移

### 13.1 StrategyStream scope

v1 `strategy_stream_key`：

```text
profile_id + feed_session_id + surface + source_platform_scope + feed_mode
```

- `surface`: `desktop_web / mobile_web / extension / cli / openclaw`；
- `source_platform_scope`: `all` 或一个 canonical platform；
- 平台 Tab 切换创建或 fork 新 FeedSession，不能让两个 scope 争同一 slot；
- 每个浏览器 tab 默认拥有新的 `client_instance_id` 和 feed session；显式恢复才共享；
- CLI/OpenClaw 在未实现完整 session/presentation 合同前保持 baseline；
- session 过期不删除计划和决策历史。Session TTL/显式关闭/Plan TTL 都走同一个 fenced cleanup 事务：ACTIVE 且未走完的 Plan 标 `EXPIRED(reason=SESSION_CLOSED|SESSION_TTL|PLAN_TTL)`，已 DELIVERED 的 Plan 在 session close 可标 `COMPLETED(reason=SESSION_CLOSED)`，Plan TTL 则仍标 EXPIRED；按 12.3.1 关闭作用域内所有 open COMMITTED/PRESENTED batches 及最后关联的 execution slots，cancel 所有 PENDING、revoke 所有 LEASED并 bump fencing。SESSION_CLOSED/SESSION_TTL 把 selecting claims 置 FAILED；PLAN_TTL 且 session/request 仍有效时则把匹配 SELECTING_DIRECTOR 转 BASELINE_FALLBACK_PENDING。随后清 active pointer/next/replan pending/临时 overlay，递增 state revision并追加逐 batch/slot + Plan/session lifecycle events。PLAN_TTL 且 session 仍活跃时同事务 enqueue `PLAN_EXPIRED` replan outbox。不能只清 pointer而遗留 active/delivered/batch 行。

Cleanup 与 commit 竞争仍按数据库顺序：commit 先赢则 cleanup 基于新 revision 保留其不可变 execution outcome 后终结其余状态；cleanup 先赢则旧 worker 因 plan status、state revision 和 fencing 全部失配而失败。这样 partial UNIQUE、restart recovery 和 session close 使用同一真相。

### 13.2 什么推进计划

只有 `ADVANCE_PLAN / APPEND_PLAN` 且 slot 的 fenced transaction 成功，才推进 `committed_through_slot_seq`。Presentation ack 不推进 cursor，但决定是否可做反馈归因。

以下永远不推进：

- `GET /api/recommendations` 的历史恢复或首屏 top-up；
- prefetch、缓存 hydration、浏览器恢复、通知抓取；
- 空池、请求失败、事务失败；
- 单卡 like/dislike/dismiss；
- 单纯切换布局或平台 tab；
- 相同 request_id 的重试。

### 13.3 迁移前置项

Director enforce 前必须先完成：

1. 将当前 side-effecting GET top-up 显式标为 `BASELINE_TOPUP`，禁止消费 plan；长期应迁移到显式生成命令；
2. 给 API 返回 `batch_instance_id`，并把现有 `mark_presented()` 接成幂等 presentation ack；
3. 为桌面 Web、移动 Web 和 extension 定义一致的 FeedSession 生命周期；
4. 对不支持新合同的调用者保留完全相同的 baseline 行为。
5. Director-aware 客户端按 `feed_session_id + batch_instance_id` 接收并呈现一个批次；legacy GET 只能在 `LEGACY_BASELINE` visibility partition 内混合历史 rows，绝不读取任何 Director-aware POST 产生的 Director/baseline/recovery rows。新 endpoint 也不能扫描全局“未处理”列表，只能在校验 client/session 后读取指定 batch。这样 POST 响应丢失或并发 legacy GET 都不能偷走新批、跨 tab 泄漏或制造无 ack 曝光。
6. 给 `/api/saved/*` 增加带 `recommendation_id + batch_instance_id` 的 durable feedback 事件桥；在此之前 save 只能作为历史收藏事实，不能做 lane attribution 或 Gate trigger。

## 14. Feedback Gate 规则

### 14.0 乱序归因队列

Director-aware click/like/save/dismiss 请求应携带 `presentation_id`，服务端可在同一事务校验 presentation 并入队；但协议仍必须容忍 behavior 比 ack 先到。每个可归因事件先写 `recommendation_director_feedback_queue(event_id PK, batch_id, recommendation_id, status, received_at, presentation_deadline, processed_gate_id)`：

- 已有合法 ack 或属于无需 presentation 的结构化 session 指令 → `READY`；
- 尚无 ack 的 item 行为 → `PENDING_PRESENTATION`，不能交给 Gate；
- ack 到达后同事务把匹配 pending 事件提升为 READY；若当前 horizon 已完成则标 `LONG_TERM_ONLY`；
- 超过 deadline 仍无 ack → `EXPIRED_UNPRESENTED`，永不作为方向 reward；
- Gate 成功应用后 → `PROCESSED` 并绑定 gate ID。

Gate 从 READY rows 原子 claim 一个 `feedback_batch_ref_id`，按 canonical event IDs 生成 `feedback_event_set_digest`。`gate_feedback_cursor` 只是最高连续 terminal watermark；存在 PENDING gap 时不得越过，非连续 READY 可由 per-event status 安全处理，不能仅靠 `after/through` 假设整个区间已消费。重复/重排事件按 event ID 幂等。必须测试 behavior-before-ack、ack-before-behavior、重复 ack/event、gap 后补齐和 horizon-close 竞争。

### 14.1 信号语义

证据优先级是：结构化用户控制 > 可确定性解析的明确事件 > 异步归一化的自由 note > 隐式行为。自由 note 尚未归一化时只执行 item 级既有反馈，不让热路径等待另一个 LLM。

| 信号 | 默认语义 | Director 动作 |
|------|----------|---------------|
| 明确指令，如“现在不看推荐算法” | 强 session topic intent | 同步 session block + REPLAN |
| “不要理论、要能动手的” | 强内容形态意图 | 先偏好 `hands_on`；只有 `content_form` 可用时才能精确 hard filter |
| dislike + 可高置信归一化 note 指向 topic/style | 强方向冲突 | 同步抑制目标 + REPLAN |
| 单条 dislike、无 note | 强 item 负向但弱方向证据 | 当前引擎即时过滤；Director 通常 PATCH/CONTINUE |
| 同一 presented PlanLane identity 的不同 item 出现 2 个显式 dislike | 方向级负向候选 | 达阈值且归因置信足够时 REPLAN |
| like | 当前兴趣的中等正向 | Director session 累积；长期 Soul 仍按现有 pipeline，stable Director profile view 只在确认阈值后变化 |
| save | 可能是“以后看”，当前 session 证据弱于 like | 累积；不单独触发转向 |
| EXPLORE slot 的同一 PlanLane identity 获得 2 个独立正向 | 新方向确认候选 | 计划结束后以 confirmed exploration 重规划 |
| dismiss | “看过/不再推该 item”，非默认兴趣否定 | CONTINUE |
| reshuffle | 满意度中性批次导航 | 不单独触发 REPLAN |
| 一次 click/短停留/跳过 | 歧义信号 | 只累计，不能单独触发 |
| 多个一致的 inferred negative | 中等方向负向 | 达阈值后 REPLAN |
| creator/item 明确排除 | 对象级限制，不自动等于 topic 否定 | 同步 filter；通常 CONTINUE/PATCH |
| pool 某目标 lane 短缺 | 执行问题 | PATCH；多个未来 slot 均不可行时 REPLAN |
| plan TTL / guardrail / taxonomy 改变 | 计划基础失效 | FALLBACK + REPLAN |

所有由 item 行为聚合出的方向级反馈必须来自已 `PRESENTED` 的 batch；显式 session 指令不受此限，但只能作用于用户明确指定的受控目标。重复事件按 event ID 幂等；retraction 追加反向事件；旧 session 的晚到反馈可进入 Soul 学习，但默认不打断当前 FeedSession。

方向归因 key 固定为 `(plan_id, plan_revision, slot_seq, lane_key)`，由服务器用 recommendation row join 得到；`lane_key="anchor_agent"` 在不同 slot/plan 可能语义不同，禁止按裸字符串跨 slot 聚合。若要跨 PlanLane 得出 topic 方向，只能再按 frozen `topic_id + taxonomy_version` 做独立聚合，并保留原 identity provenance。

### 14.2 决策优先级

```text
hard safety / scope mismatch / stale lease
  > explicit user intent conflict
  > durable plan basis invalid
  > repeated direction-level feedback
  > current-slot shortage
  > ordinary continuation
```

### 14.3 冷却与防抖

- 默认同一 StrategyStream 的普通 replan cooldown：60 秒；
- 明确用户指令与 hard-policy 变化可绕过 cooldown；
- cooldown 中收到多个信号时合并为一个最新 session version；
- 同一 StrategyStream 同时最多一个 planning task；
- task 运行时新的强反馈只更新 desired context version；旧回包若 basis 落后则丢弃并按最新版本重跑一次；
- 每个 session 最多 4 次正常 Planner provider 调用，另有最多 2 次由 batch commit 或弱反馈 CONTINUE Gate 独自造成 stale 的 context-churn 重试；实际 provider 调用总数最多 6 次，超过后使用 Gate + baseline 到 session 结束（18.1）。
- 普通弱信号使用 hysteresis：达到进入阈值后，必须跌破更低的退出阈值才解除抑制，防止来回震荡。

阈值是 v1 初始安全值，只有在 DecisionLog 中观察足够事件后才能调整，不能隐藏在 prompt 中。

#### 14.3.1 `director-gate.v1` 确定性判定表

Gate policy 是代码拥有的版本化 registry；版本进入 DirectorInput/Plan/Gate basis、planning_key、gate_key、`gate_decided.v1` 与 runtime execution config。任何阈值或优先级变化须升级版本并递增 runtime epoch。每次判定只使用同一事务冻结的 READY feedback event set、当前 PlanLane identity、capability/slot 状态和 session projection，按下表自上而下匹配第一项；相同输入必须生成相同 action/strength/reason。`PATCH` 只在目标 slot 为 PENDING 且修补后 feasibility 仍成立时输出，否则升为 REPLAN。

| 条件（同一 FeedSession） | action / strength | reason |
|---|---|---|
| 当前 hard guard、scope 或 taxonomy/recipe/guardrail/Gate policy 与可执行 Plan 不兼容 | `REPLAN / HARD`；无 active Plan 则 `FALLBACK / HARD` | `PLAN_BASIS_INVALID` |
| 用户结构化明确 block 一个受控 topic/style，或明确方向冲突的归一化 session 指令且 confidence ≥ 0.90 | `REPLAN / STRONG` | `EXPLICIT_SESSION_INTENT_CONFLICT` |
| 同一 PlanLane 在 10 分钟内有至少 2 个不同 `item_key` 的已呈现显式 dislike | `REPLAN / MEDIUM` | `REPEATED_EXPLICIT_NEGATIVE` |
| 同一 PlanLane 在 10 分钟内有至少 3 个不同 `item_key` 的已呈现 inferred negative，且每条 confidence ≥ 0.75 | `REPLAN / MEDIUM` | `REPEATED_INFERRED_NEGATIVE` |
| 未来 ≥ 2 个 PENDING slot 缺少 minimum，或当前 slot 无合法 fallback lane 可恢复 minimum | `REPLAN / MEDIUM` | `MULTI_SLOT_SHORTAGE` |
| 仅 1 个 PENDING slot 缺 minimum，受控 fallback lane 可恢复 | `PATCH / MEDIUM` | `SINGLE_SLOT_SHORTAGE` |
| 单个已呈现 item 显式 dislike，未达到方向阈值 | 可行则 `PATCH / WEAK`，否则 `CONTINUE / WEAK`；item 级排除立即执行 | `SINGLE_ITEM_NEGATIVE` |
| EXPLORE PlanLane 在 15 分钟内有至少 2 个不同 `item_key` 的已呈现正向确认 | `CONTINUE / MEDIUM`，在可回放的 feedback aggregation 中确认该 PlanLane，供下个 horizon 的 `recent_batches` 读取 | `EXPLORE_CONFIRMED` |
| 其余合法输入 | `CONTINUE / WEAK` | `NO_DIRECTION_CHANGE` |

计数先按 `(PlanLane, canonical item_key)` 去重：同一 item 的 like、save、重复点击最多贡献一份正向，优先采用最新未撤回的显式事件；同一 item 的重复 dislike 也只算一份。显式 session 指令不伪装成 item 曝光。retraction/correction 重算受影响窗口，但不改写已提交 Gate；若仍有 open Plan，则以新 event set 产生新 Gate。方向抑制进入后，须在最后一次负向之后 10 分钟无新负向、且收到至少 2 个不同 item 的已呈现正向，才解除普通行为抑制；显式 block 只能由用户明确撤销或 TTL 到期解除。窗口使用服务端接收时间、左闭右闭，边界相等时计入；并列 reason 按上表顺序取首项。未达到阈值的 save、click、dismiss、reshuffle 只记证据，不触发重规划。

`director-reasons.v1` 必须至少登记上表的九个 reason code、`PATCH_OVERLAY_EXPIRED` 和本规格各 lifecycle/repair code；不同 event family 使用闭合子集。Gate 的 `reason_codes` 在 v1 恰好含命中的首个 code，不能由模型补充解释。确认/抑制聚合的来源 event IDs、PlanLane、窗口起止与 digest 要可 replay；同一 item 的 like+save、重复 dislike、撤回、窗口边界和两条独立正向的性质测试是 Gate 上线条件。

### 14.4 Planner 触发矩阵

| 触发 | 是否调用 Planner | 热路径行为 |
|------|------------------|------------|
| 新 FeedSession 且无 plan | 异步调用 | 当前请求可 baseline；计划供下一次 advance |
| horizon exhausted | 异步调用 | baseline 或 safe anchor，不等待 |
| 用户结构化“换方向/不看 X” | 绕过普通 cooldown，single-flight replan | 同步 overlay + safe recovery/baseline |
| 多个已 presented 的一致弱信号达阈值 | cooldown/debounce 后调用 | 当前有效 plan 可继续，除非 overlay 冲突 |
| stable profile policy revision 改变 | 调用 | 旧 plan 不再激活；已 committed 不变；raw Soul generation 变化但 stable digest 不变时不调用 |
| capability digest 实质变化且未来 slot 不可行 | 调用 | 当前先 PATCH/baseline |
| 单 click/save/dismiss/reshuffle | 通常不调用 | 当前 session 累计或中性导航；只有 stable profile reducer 真正跨确认阈值时才落入上一行 |
| plan TTL 到期 / horizon 用完 | 调用 | baseline，不等待 |

Planner 未就绪永远不是拒绝推荐请求的理由。

## 15. Policy Compiler 算法

### 15.1 编译步骤

1. 在 request claim 已建立后，只 CAS lease `DirectorSessionState.next_slot_seq` 指向的那个 PENDING PlanSlot，取得 fencing token；禁止扫描并跳过一个已 LEASED 的 next slot 去消费后续 slot。
2. 通过按目标 lane 定向的 Candidate Bus API 获取 fresh、精确的 `SERVING CandidateSetRef`；不能只复用 relevance top-40。
3. 验证 plan、taxonomy、recipe、guardrail、session overlay 和 candidate digests。
4. 令 `r = requested_limit / reference_batch_size`；每个 PlanLane 先算 raw 值，再固定 `scaled_min=floor(raw_min)`、`scaled_max=min(requested_limit, ceil(raw_max))`，并验证 `sum(min)<=limit<=sum(max)`。
5. target 先对 `raw_target` 做 Hamilton：各 lane 取 floor，再按小数余数降序分配剩余席位，余数相同按 canonical `lane_key` 升序。随后 clamp 到 `[scaled_min, scaled_max]`；若总和偏小，反复给 `scaled_max-current` 尚有容量且 `(raw_target-current)` 最大的 lane `+1`；若偏大，反复给 `current-scaled_min` 尚可减少且 `(current-raw_target)` 最大的 lane `-1`，所有并列仍按 lane_key 升序。最终必须 `sum(target)=requested_limit` 且 `min<=target<=max`，否则 compiler 失败而非猜测。style soft target 使用相同版本化 rounding helper，但不改变 hard lane sum。
6. 在 seen/dislike/platform/admission 和现有 guardrail policy 下计算联合可行性；每个候选最多归属一个 lane。
7. 先保留所有 lane 的 minimum capacity，再向 target 分配，最后只在必要时到 max。
8. 按显式 `fallback_lane_keys` 搬移短缺；style 始终只是 v1 软目标。
9. 输出纯函数式 CompiledPolicy 和完整 compiler trace；同一 compile key 结果一致。
10. Composer 后由 FinalPolicyVerifier 对真正准备提交的 rows 再验收。

### 15.2 Shortage repair

修补必须遵循：

```text
同一 PlanLane 的其它 topic_id
  → slot 声明的 adjacent fallback lane
  → anchor lane（不超过 max）
  → recipe registry 的系统 fallback
  → abort Director attempt；用 fresh normal snapshot 另起纯 baseline substitution
```

禁止：

- 从未声明的新 topic_id 自由补位；
- 为凑满数量放松 dislike、seen、platform 或 amplification guard；
- 静默超过 lane max；
- 让 Director 改写现有 selector 的 cap/relaxation 规则；
- 用未来 slot 的 item 预占当前库存。

若修补后满足少于 `requested_limit`，且每个 lane minimum 仍全部满足：

- 允许返回合法的较短批次；
- 记录 `UNDERFILLED_AFTER_HARD_GUARDS`；
- 不以跨平台或已看内容补齐；
- 可以发受控 supply advisory，但不阻塞响应。

任一 lane minimum 不满足时禁止走上述短批路径，必须按 `WHOLE_BATCH_BASELINE_IF_ANY_MINIMUM_FAILS` 丢弃 Director selection并做纯 baseline substitution。

### 15.3 Composer 选择顺序

建议 v1 使用“约束分配 + 全局序列化”的确定性两阶段方法：

1. 在每个 lane 内沿用 Curator score，构建候选与 lane 的二部图；
2. 用带 minimum reservation 的确定性 constrained greedy（必要时用小规模 flow repair）完成唯一 lane attribution；
3. 全局按 MMR 和既有 topic/style/broad-topic/amplification policy 排序，持续检查剩余 minimum 是否仍可满足；
4. 执行现有可访问入口替换与 interleave；替换后重新计算所有约束；
5. FinalPolicyVerifier 记录 planned/realized lane mix 与每次 repair；
6. 每条 recommendation 保存 `lane_key`，供反馈归因。

当前 10 条批次的 broad-topic 初始 cap 为 3，候选读取默认每个 `topic_group` 最多 3 条；无 embedding 的非-MMR fallback 可能按既有退让放宽到 6，而 MMR 路径保持初始 cap。因此 Director lane 必须由多个 fine topic_id 组成，不能有意要求单主题 5–6 条；实际 cap 与 fallback 始终由 `guardrail_policy_version` 拥有，不硬编码进 Prompt，也不能把 fallback 当常规配额能力。

## 16. 完整端到端例子

### 16.1 初始状态

长期画像：

- Agent 工程、软件系统和 AI 产品设计为稳定兴趣；
- 喜欢实操与系统分析；
- exploration openness 中等。

FeedSession 状态：

- 最近连续查看 MCP、Coding Agent；
- 点赞并收藏一条 Agent Memory 工程实践；
- 明确说“最近想看能做出来的东西”。IntentProjector 将其转成 session `hands_on` 强偏好；因为尚无 `content_form`，系统不声称能精确过滤所有论文解读。

规划时 capability summary：

| topic family（由多个 topic_id 组成） | 可服务数 |
|---------------------------------------|----------|
| Agent memory / tooling / observability | 31 |
| Developer workflow / local AI tooling | 18 |
| Recommendation feedback / feedback systems | 13 |
| AI product design / product analytics | 14 |
| HCI / adaptive interfaces | 9 |

### 16.2 Director 计划

```text
Slot 18 / focused_practical → DEEPEN
  anchor_agent 5（跨 memory/tooling/observability）
  adjacent_workflow 3（跨 developer_workflows/local_ai_tooling）
  adjacent_feedback 2（跨 recommendation_feedback/feedback_systems）

Slot 19 / balanced_bridge → BRIDGE
  anchor 4 + feedback bridge 4 + HCI seed 2

Slot 20 / novelty_probe → EXPLORE
  familiar anchor 4 + adjacent product 3 + novel HCI 3
```

这不是提前选好 30 条内容；只有 Slot 18 会在请求到来时绑定 fresh SERVING CandidateSetRef。

### 16.3 第一个 batch 执行

客户端用 `ADVANCE_PLAN + request_id=req_18` 请求。Store lease Slot 18；Compiler 验证三条 lane 的联合供给后输出 5/3/2 目标。Composer 按 Curator + constrained selection + MMR 选十条，最终实际 fine-topic mix 为 2/2/1、2/1、1/1，没有单 topic 超过现有 cap。

短事务同时写入 recommendations、pool shown、BatchInstance `COMMITTED`、Slot 18 cursor 和 DecisionLog。响应携带 `batch_instance_id`。客户端实际渲染后发送幂等 presentation ack，状态才变为 `PRESENTED`。

每条 recommendation 额外绑定：

```text
batch_instance_id, plan_id, plan_revision, slot_seq,
derived_role, recipe_id/version, lane_key, compiled_policy_id
```

Expression 可以写一条屏级导语：

> 先顺着你刚才对 Agent Memory 实现细节的兴趣往下走，这一组偏能落地，后面再逐步打开到反馈系统。

但每张卡仍使用既有单条推荐文案。

### 16.4 用户反馈 A：方向被否定

第一个 batch presented 之后用户：

- 喜欢 1 条 Agent Memory；
- 收藏 1 条本地 Agent 项目；
- 对推荐算法内容明确说“现在不想看算法，想看产品里怎么接反馈”。

Gate 得到：

```text
action = REPLAN
reason = EXPLICIT_SESSION_INTENT_CONFLICT
session block = [recommendation_feedback]
invalidate_from_slot_seq = 19
```

已 committed 的 Slot 18 不变。旧 Slot 19/20 仍为 PENDING，因此被 cancel/supersede，新计划从 19 开始：

```text
Slot 19 bridge
  Agent family 4 + product feedback implementation 3 + AI product family 3

Slot 20 explore
  AI product family 4 + HCI family 3 + familiar anchors 3

Slot 21 deepen
  Product feedback implementation 4 + AI product family 3 + familiar anchors 3
```

如果新计划尚未完成而用户立即请求下一批，系统执行 `recovery` 静态策略，排除 session suppression，不执行旧 Slot 19 中相反的算法方向。

### 16.5 用户动作 B：只是换一批

如果用户没有逐条负反馈，只点了“换一批”：

- existing API 继续记录 satisfaction-neutral `reshuffle`；
- Director-aware 客户端发送 `STAY_SAME_DIRECTION`，复用当前 recipe/lane 生成 ephemeral patch batch，不消费 Slot 19；
- Gate 不因为 reshuffle 本身判定用户否定主题；
- 用户选择“继续这条线”或 append 时才发送 `ADVANCE_PLAN / APPEND_PLAN`，进入 Slot 19。

### 16.6 用户反馈 C：探索获得正向

Slot 20 的 HCI lane 有两条分别获得 like 和 save（这里假设 Phase 0 已把 saved action 以 `recommendation_id + batch_instance_id` 接入 EventIngress；未接线时 save 不得进入 Gate）：

- 可回放的 feedback aggregation 将该 PlanLane 标记为 `candidate_confirmed`，下次 Context Builder 从聚合读取；
- 如果当前计划已结束，下一次 plan 使用 `trigger.type = HORIZON_EXHAUSTED`，并携带 server-owned reason `CONFIRMED_EXPLORATION`；
- 新方向先进入下一计划的高-anchor deepen arm，而不是立即写入长期 profile；
- 后续由现有 Soul/cognition 根据更多证据决定是否长期晋升。

## 17. 存储设计

建议新增以下逻辑对象；具体 migration 按现有 SQLite 规范落地。

### 17.0 `recommendation_director_runtime`

全库单行保存 `mode + director_runtime_epoch + execution_config_digest + updated_at + change_reason`。epoch 是正整数，任意 `off/shadow/enforce` 切换、kill switch 或会改变可执行语义的 Director 配置重载都在同一短事务 `+1`；这是所有 worker 的权威值，不能只靠进程内配置。旧 epoch artifact 永远只读。

### 17.1 `recommendation_feed_sessions`

| 字段 | 说明 |
|------|------|
| `feed_session_id` PK | 会话 ID |
| `profile_id/client_instance_id` | 用户和客户端实例 |
| `surface/source_scope/feed_mode` | 权威 StrategyStream scope |
| `state/created_at/last_activity_at/expires_at/activity_revision` | 生命周期；activity CAS 与 Director state revision 分离 |
| `assignment_id` NULLABLE | 当前 Director experiment assignment 快速指针 |

### 17.2 `recommendation_profile_views`

| 字段 | 说明 |
|------|------|
| `profile_id` PK | 推荐画像命名空间 |
| `profile_policy_revision` | 推荐语义投影变化时单调递增 |
| `profile_view_digest/view_json` | 只含允许进入推荐的 compact typed view |
| `source_generation/updated_at` | reducer 输入代际与时间 |

该 reducer 通过 durable change feed/轮询更新，不占用现有 `MemoryManager.set_profile_change_callback()` 单回调槽。

#### 17.2.1 `recommendation_experiment_assignments`

| 字段 | 说明 |
|---|---|
| `assignment_id` PK | 不可变 assignment |
| `experiment_id/unit_type/unit_hash` | 随机化单元；主因果实验为 PROFILE_TIME_BLOCK 的脱敏 hash |
| `eligible_actions_json/digest` | 分流时实际可选动作集合 |
| `assigned_action/assignment_probability` | 实际动作与 propensity |
| `rollout_stage/execution_horizon_limit` | assignment 时冻结；Phase 2 director arm 为 1 |
| `assignment_policy_version/assigned_at` | 可 replay 版本与时间 |

唯一约束为 `(experiment_id, unit_type, unit_hash)`，确保重启、重试和多进程都 O(1) 读回同一分组，不重新抽签。创建 assignment、写 FeedSession pointer 与追加 `EXPERIMENT_ASSIGNED` 必须同一事务；实验配置改变必须使用新 experiment/version，不能覆盖原行。执行时永远读取 assignment 固化的 rollout limit，不能因进程重启或全局配置升到下一 Phase 就继续消费旧 Plan。

### 17.3 `recommendation_planning_requests` 与 proposals

`recommendation_planning_episodes` 是 outbox 与工程分母的权威：`planning_episode_id`、StrategyStream、runtime epoch、created/accept/closed times、`desired_context_version`、`trigger_refs_digest`、合并后的 reason codes、status (`QUEUED / RUNNING / ACCEPTED / REJECTED / STALE_REJECTED / FAILED / DEFERRED`)、terminal reason 和 accepted plan ref。每个 StrategyStream 最多一行未终态 episode；新触发在该行原子合并并提升 desired version，不因一次点击制造另一个分母。episode 在 Input 冻结前可因预算、高 churn、session close 或 runtime off 进入 DEFERRED，须同事务写 `PLANNING_DEFERRED`；关联的请求终态和 episode 终态也同事务落定。统计以 terminal episode 为单位，已合并的原始 trigger refs 只作 provenance，不重复计覆盖率。

`recommendation_planning_requests` 保存 `planning_request_id`、`planning_episode_id` FK、UNIQUE planning_key、mode、canonical DirectorInput/input_digest、status、accept_until、attempt、claim owner/lease/fencing、provider route 和终态 proposal/plan ref。状态为 `QUEUED / RUNNING / ACCEPTED / REJECTED / STALE_REJECTED / FAILED`；一个 episode 可因 context-churn 有最多 3 个顺序请求，但同一时刻只有一个 RUNNING。跨进程 worker 通过 lease claim，同 key 只允许一个权威终态。最终 provider timeout/401/invalid JSON 等 FAILED 与 `PLANNING_FAILED(planning_failed.v1)` 同事务；有界 retry 的中间 attempt 可进 operational attempt table，但不能覆盖终态原因。

`recommendation_director_proposals` 保存 `proposal_id`、planning_request_id、output_digest、canonical Proposal、model/prompt provenance、validation status/codes 和 received_at。合法、拒绝和 stale Proposal 都保留脱敏审计；LLM 回包先落 proposal/validation，再由激活 CAS 决定是否产生 Plan，不能只靠 plans.planning_key 在回包前 single-flight。

### 17.4 `recommendation_director_plans`

| 字段 | 说明 |
|------|------|
| `plan_id` PK | 不可变计划 ID |
| `feed_session_id` | StrategyStream scope |
| `revision` | session 内单调版本 |
| `director_runtime_epoch` | 创建该 plan 的全局运行 epoch |
| `mode/status` | shadow/enforce；shadow_validated/shadow_evaluated/validated/active/delivered/completed/expired/superseded/failed |
| `basis_json` | 版本与 digest |
| `assignment_id/execution_horizon_limit` | rollout 执行上限；非实验可为空 |
| `plan_json` | canonical validated plan |
| `schema/model/prompt/recipe_version` | replay 版本 |
| `created_at/expires_at` | 生命周期 |
| `superseded_by/error_code` | 结束原因 |

唯一约束：`DirectorSessionState.active_plan_id` 是执行权威指针；数据库 partial UNIQUE 确保每个 session 在 `status IN ('active','delivered')` 中最多一行，避免 DELIVERED 与新 ACTIVE 并存；`feed_session_id + revision` 唯一；`planning_key` 唯一。

### 17.5 `recommendation_director_slots`

| 字段 | 说明 |
|------|------|
| `plan_id + slot_seq` PK | 计划 slot |
| `status` | pending/leased/committed/substituted/presented/closed/canceled/revoked |
| `lease_owner/lease_request_id/expires_at/fencing_token/slot_revision` | 多进程 lease、request claim 与 CAS |
| `applied_gate_decision_id/patch_overlay_digest` | PATCH binding；必须成对为空或存在 |
| `stay_patch_count` | origin slot 原子计数；受配置上限约束 |
| `execution_outcome/substitute_reason` | Director commit 与 baseline substitution 的不可变来源；presentation 后仍保留 |
| `committed_batch_id` UNIQUE | Director 提交或 baseline substitution 批次 |
| `presented_at/closed_at/cancel_reason/revoke_reason` | 生命周期 |

### 17.6 `recommendation_candidate_sets` 与 members

- ref 表保存 scope、purpose、版本、digests、capability summary、TTL；
- 跨来源候选的 canonical 身份只能使用 `DiscoveredContent.item_key`；`member_digest` 对 canonical-sorted `item_key` 列表求 hash，禁止使用 legacy `bvid` 或裸 `content_id`；
- PLANNING ref 可只持久化 aggregate、版本和 digest；SERVING ref 才在短 TTL member 表保存 exact `item_key + frozen execution features`，仅服务端可读；
- 短 TTL 后清理 exact members，长期只保留 digest 与匿名聚合；
- 清理后仍可重放 plan validation、compiler arithmetic、状态转换和已实现结果，但不能精确重跑当时 baseline/shadow selector；需精确选择 replay 时使用版本化脱敏 fixture，或在用户启用 debug sampling 后把最小 frozen execution-feature snapshot 保存到单独、明确 TTL 的分析表；
- `(purpose, scope, pool_generation, member_digest, feature_digest, expires_at)` 建普通查询索引；builder 可在事务中复用仍未过期的 exact match，但不建跨 TTL 的永久 UNIQUE。过期 ref 永不复活，新 generation/新 TTL 必须能创建新 ref。

### 17.7 `recommendation_compiled_policies`

保存 canonical CompiledPolicy、`compile_key`、binding、versions、degradation 和 TTL；`compile_key` 唯一。它不保存候选标题或用户原文。

同一 migration 组还包含：

- `recommendation_constraint_sets` 与短 TTL excluded-item/amplification-guard members：保存 typed payload、digest、scope、versions、TTL、exact canonical item_key 和 request-specific guard keys；
- `recommendation_shadow_evaluations`：只存不可执行评估 artifact，DAO 不暴露给 commit 路径；
- `recommendation_recovery_policies`：保存 server-owned recovery artifact 与 reservation binding；
- 对上述 key 分别唯一，所有过期 artifact 只读审计、不可复活。

`recommendation_gate_decisions` 保存 `gate_decision_id`、UNIQUE gate_key、完整 canonical typed artifact、expected/applied state revision、cursor range、action、reason codes 和 `status=APPLIED`。因为 Gate create+apply 原子化，权威表不允许 PENDING；事务失败不留行。CompiledPolicy 的 gate ref 必须能在这里解析并核对 overlay digest/valid_until。

### 17.8 `recommendation_batches`

| 字段 | 说明 |
|------|------|
| `batch_instance_id` PK | 每次生成批次 |
| `operation_id` UNIQUE NOT NULL | 所有新旧路径的服务端追踪 ID |
| `request_id` UNIQUE NULLABLE | Director-aware 客户端全局幂等；legacy 为 null |
| `director_runtime_epoch` NULLABLE | Director-aware request claim 冻结 epoch；legacy 为 null |
| `consumed_gate_decision_id/override_mode/override_valid_until` | 一次性 override 消费 provenance；三者全空或全有且提交后不可改 |
| `request_fingerprint_version/digest` | 完整 request-fingerprint.v1 行为字段冲突校验；含 client/surface/mode/previous/restore/assignment |
| `feed_session_id` | Director-aware StrategyStream scope；legacy 可为空 |
| `visibility_partition` | LEGACY_BASELINE 或 DIRECTOR_SESSION；决定哪个 endpoint 可读取 |
| `batch_intent` | advance/append/stay/topup/one-shot |
| `plan_id/revision/slot_seq` | 可为空表示 baseline |
| `origin_slot_seq/origin_batch_id/patch_sequence` | stay patch 来源 |
| `batch_reservation_id` | STAY/recovery 权威 reservation ref；PLAN_SLOT 为空 |
| `selection_owner/lease_expires_at/fallback_deadline/fencing_token` | selecting/fallback-pending request claim 的 crash recovery |
| `execution_policy_type/execution_policy_id/candidate_set_ref_id` | COMPILED_POLICY / RECOVERY_POLICY / NONE 判别联合与执行快照绑定 |
| `requested_execution_kind/committed_execution_kind/execution_outcome` | requested 固定为 PLAN_SLOT / STAY_PATCH / SAFE_RECOVERY / NORMAL_BASELINE；committed 仅成功时写，见下表；outcome 是不可变归因值 |
| `requested_mode/committed_mode/status` | requested mode 不改写；committed mode 仅提交时写 baseline/enforce/recovery；status 为 selecting_director/selecting_recovery/baseline_fallback_pending/baseline_selecting/committed/presented/closed/failed |
| `feedback_accept_until/presented_at/closed_at/close_reason/presentation_status` | frozen 控制反馈 deadline 与曝光/关闭生命周期 |
| `realized_mix_json/fallback_codes` | 聚合执行结果 |

STAY rows 对 `(origin_batch_id, patch_sequence)` 建 UNIQUE，且 origin_batch_id 始终指向 root Director batch而非上一 patch；previous batch 链尾另由 request fingerprint/current batch 校验。

请求开始时冻结 `requested_execution_kind`；成功提交时按下面的闭合映射写 `committed_execution_kind + execution_outcome + committed_mode`。失败行的 committed 三元组必须全空；COMMITTED/PRESENTED/CLOSED 行必须全有且以后不可改。Ack、reward 和 DecisionLog 按 committed kind 判别，并逐值校验这三个字段，不从 mode 或 nullable refs 猜测。

| requested → committed | outcome / mode | policy 与归属 CHECK |
|---|---|---|
| `PLAN_SLOT → PLAN_SLOT` | `DIRECTOR_COMMITTED / enforce` | COMPILED_POLICY，plan/slot 非空，无 reservation |
| `PLAN_SLOT → PLAN_SLOT_SUBSTITUTED` | `BASELINE_SUBSTITUTED / baseline` | NONE，plan/slot 与原有效 lease 非空，fresh baseline ref，Slot 同事务 SUBSTITUTED |
| `STAY_PATCH → STAY_PATCH` | `STAY_COMMITTED / enforce` | COMPILED_POLICY，origin root/patch sequence 与 reservation 非空，next slot 不变 |
| `SAFE_RECOVERY → SAFE_RECOVERY` | `RECOVERY_COMMITTED / recovery` | RECOVERY_POLICY，reservation 与 consumed Gate 非空，plan/slot 为空 |
| `STAY_PATCH / SAFE_RECOVERY / PLAN_SLOT → NORMAL_BASELINE` | `BASELINE_COMMITTED / baseline` | NONE，Director lease/reservation 已清理，plan/slot 为空，保留 requested kind 与 Gate/失败 provenance |
| `NORMAL_BASELINE → NORMAL_BASELINE` | `BASELINE_COMMITTED / baseline` | NONE，plan/slot/reservation 为空；Director-aware 有 fresh baseline ref |

上述行是数据库 CHECK 可表达部分与事务 validator 的 XOR 合同；其余组合一律拒绝。`PLAN_SLOT → NORMAL_BASELINE` 只在原 slot 已被 fenced revoke/cancel 后发生；仍持有有效 slot 的执行缺货必须走 `PLAN_SLOT_SUBSTITUTED`。Legacy baseline 允许 candidate ref 为空，且 requested/committed kind 均为 NORMAL_BASELINE。不能把 recovery ID 塞进 `compiled_policy_id` 或靠 mode 猜表。

`recommendation_batch_reservations` 是 STAY/RECOVERY 的权威执行租约：`batch_reservation_id` PK、`request_id` UNIQUE、`director_runtime_epoch`、owner、status (`HELD / COMMITTED / EXPIRED / CANCELED`)、fencing_token、expires_at、batch_instance_id。获取/重获使用 CAS 且 token 单调；CompiledPolicy/RecoveryPolicy 绑定此行。Batch 表保存 reservation ID，不能只复制 token/expiry 后失去 owner/status 真相。

`recommendation_generation_claims` 是跨 PLAN_SLOT/STAY/RECOVERY/baseline 的 session mutex：保存 `generation_claim_id`、feed_session_id、request_id、batch_instance_id、claim_kind、status (`ACTIVE / COMMITTED / FAILED / EXPIRED`)、owner/fencing/expires_at。`UNIQUE(request_id)` 且 partial `UNIQUE(feed_session_id) WHERE status='ACTIVE'`；Batch claim、slot lease或reservation与该行在同一事务创建，fallback pending/接管只 bump fencing 不释放，Batch commit/fail 与 claim terminal 同事务。不能试图用分别位于 slots 与 reservations 两张表的 UNIQUE 近似这个跨路径约束。

### 17.9 `recommendation_presentations` 与 items

`recommendation_presentations` 保存 `presentation_id` PK、`batch_instance_id`、`feed_session_id/client_instance_id`、`payload_digest`、`presentation_status`、client_presented_at 和 server_received_at；`recommendation_presentation_items` 以 `(presentation_id, recommendation_id)` 为主键保存 `server_rank_position + rendered_position`，并对 `(presentation_id, rendered_position)` UNIQUE。同一 presentation ID 同 payload 幂等，不同 payload 返回冲突。多个 PARTIAL ack 可独立追加，分析时按 `(batch_instance_id, recommendation_id)` 去重曝光、保留第一次合法 rendered position；Batch 的 COMPLETE/PARTIAL/UNKNOWN 由这些不可变 ack 聚合，不能靠覆盖一个数组实现。

同组 migration 增加 `recommendation_director_feedback_queue` 的逐 event 状态、presentation deadline、claim batch ref 和 processed gate ref；索引 `(feed_session_id, status, event_id)`。它是乱序归因权威，不以单个 cursor 代替 pending gap。

### 17.10 `recommendation_director_state`

每个 FeedSession 一行小状态：

- state revision；
- active plan/revision；
- next/committed-through slot seq；
- current actionable batch instance；
- normalized current intent digest；
- temporary suppression ref/digest + TTL；
- gate-consumed feedback cursor；
- applied gate id/key、一次性 next-batch override、replan pending version、invalidate-from slot；
- assignment rollout-exhausted flag；
- `state_revision`（本行唯一 optimistic-lock 版本）；
- `session_policy_revision`（只在语义 session intent 变化时随 state revision 一起递增）。

它是语义快状态，不替代 event log 或 Soul/Profile。另建 `recommendation_director_operational` 保存 LLM budget window、`plan_calls_used/provider_calls_total/context_churn_retries_used`、last replan requested 与独立 `operational_revision`；last activity/expiry 由 FeedSession + activity revision 拥有。feedback PlanLane counters 留在 projector/feedback queue 的可回放聚合，不混入 control-plane revision。

### 17.11 `recommendation_director_events`

append-only 保存 planner、validator、lease、gate、compiler、assignment、fallback、presentation 和 reward 事件。禁止 UPDATE 修改历史语义；更正一律追加 correction/retraction event。

### 17.12 现有 `recommendations` 的最小扩展

推荐记录需要能追溯：

```text
batch_instance_id
director_plan_id / revision / slot_seq
director_recipe_id / version
director_lane_key
execution_policy_type / execution_policy_id
server_rank_position             # 1-based；UNIQUE(batch_instance_id, position)
visibility_partition             # LEGACY_BASELINE | DIRECTOR_SESSION
```

可以使用显式列或版本化 metadata，但必须能在 feedback 写入时无需重新推断地取得这些字段。`visibility_partition` 必须是可索引、数据库约束的显式列：`DIRECTOR_SESSION` 要求非空 batch/feed_session；legacy GET 查询硬编码只读 `LEGACY_BASELINE`，不能依赖调用方事后过滤 metadata。

## 18. 并发、幂等与一致性

### 18.1 请求幂等

- Director-aware 客户端对“生成新批次”提供全局唯一 `request_id`；legacy 路径只有 server `operation_id`；
- 相同 `request_id` 重试返回同一 BatchInstance 或同一终态失败；
- 不能重复消费 slot，不能重复标 shown；
- `request_id` 的任一 `request-fingerprint.v1` 行为字段不同时返回冲突错误；数据库使用 `UNIQUE(request_id) WHERE request_id IS NOT NULL`，不是假设 legacy 都有复合键。
- 每个生成请求按 execution branch 原子创建 `BatchInstance(status=SELECTING_DIRECTOR|SELECTING_RECOVERY|BASELINE_SELECTING)` 作为 request claim；相同 request 的并发重试只读取、等待或按规则接管这个 in-progress/terminal 对象。不同 request 若发现本 session 的 exact next slot 已 LEASED，返回 `SESSION_ADVANCE_IN_PROGRESS` 及正在选择的 batch ref，不得去 lease 后一个 PENDING slot。
- 更强的不变量是每个 FeedSession 同时最多一个 active generation claim，覆盖 SELECTING_DIRECTOR、SELECTING_RECOVERY、BASELINE_FALLBACK_PENDING、BASELINE_SELECTING 以及 STAY/RECOVERY reservation；fallback pending 全程不释放该 mutex。相同 request 可读取/接管原 claim，不同 request 一律返回 `SESSION_GENERATION_IN_PROGRESS + active_batch_instance_id`，不能消费 next-batch override、创建另一 reservation 或并发提交第二个 current batch。只有 claim 对应 Batch COMMITTED/FAILED 后才原子释放。

Planner trigger 可以在 generation claim 活跃时入 durable outbox，但 Context Builder 必须等该 claim COMMITTED/FAILED、`current_batch_instance_id` 和 state revision 已确定、当前 READY feedback 已由 Gate 处理后，才冻结 DirectorInput；对 session-start、horizon-exhausted、feedback replan 一律如此。worker 再等待 750 ms 的无新 generation claim/READY feedback 安静窗口，合并其间 trigger 为最新 desired version；不阻塞当前请求。规划期间若新的 baseline/recovery batch 或只记录弱反馈的 CONTINUE Gate 先提交，Plan activation CAS 按既有规则 stale，不能忽略新 context 继续激活旧计划。只有这些 non-policy context mutation 是唯一差异时才合并为一次 latest-context retry：provider 调用记真实成本与 `provider_calls_total`，但不扣正常 `plan_calls_used`。每 session 最多 4 次正常 provider 调用、另最多 2 次 context-churn retry，且 `provider_calls_total ≤ 6`；超过上限或持续没有安静窗口时本 session 安全走 baseline，等待下个 session 再尝试。profile/session policy、taxonomy、runtime epoch、REPLAN/PATCH 等变化造成的 stale 属正常预算，不冒充 context-churn。队列等待、无 provider 调用的合并和取消不计调用预算。必须测试 planner 请求与 baseline/recovery commit、弱 Gate 的两种串行顺序、连续快速请求、预算上限和 stale retry 幂等。

### 18.2 Plan 验收与 ENFORCE activation CAS

异步规划回包先经过 shared accept/validation predicate：

```text
feed session still active
AND now <= planning_request.accept_until
AND global director epoch == response.basis.director_runtime_epoch
AND mode compatibility holds (ENFORCE response requires ENFORCE；SHADOW allows SHADOW or configured shadow sampling under ENFORCE)
AND director_state_revision == response.basis.director_state_revision
AND profile/session policy revisions unchanged
AND taxonomy/recipe/guardrail/Gate-policy versions compatible
AND planning inventory passes the v1 predicate below
```

否则保存为 stale/failed audit，但不影响 active plan。仅 ENFORCE 分支再要求 `no newer revision active` 与 current active pointer/supersedes boundary 匹配；SHADOW 不读取或竞争 execution pointer。

v1 不使用模糊的“acceptable”阈值：current `inventory_digest` 必须精确等于 basis digest。若 pool member/feature digest 变化但量化 inventory digest 仍相同，服务器必须构建 fresh PLANNING ref、对全部三个 slots 重跑 Feasibility Validator，并在对应 PlanEnvelope/DecisionLog 记录 `validation_candidate_set_ref_id + member/feature_digest`；通过才可 accept。inventory digest、taxonomy、recipe、guardrail、Gate policy、profile/session revision 任一变化都直接 stale reject，不让 validation 临时猜容差。

分支随后严格分离：

- SHADOW：只写 immutable Plan + `ShadowEnvelope(SHADOW_VALIDATED)` 与 `PLAN_ACCEPTED(mode=SHADOW)`；不写 active pointer、不创建 PlanSlotState、不增 state revision。Evaluator 完成后写 SHADOW_EVALUATED/POLICY_EVALUATED_SHADOW，失败则写下文 typed shadow failure；
- ENFORCE：activation 成功本身是 control-plane mutation；同一事务写 active plan pointer、创建 slot states、处理旧 PENDING/LEASED slots、追加 `PLAN_ACTIVATED`，并将 `state_revision + 1`。后续 CompiledPolicy 必须绑定这个 post-activation revision，而不是 Plan 的 pre-activation basis revision。

### 18.3 Lease、选择与 fenced commit

执行入口是严格判别联合 `PLAN_SLOT | STAY_PATCH | SAFE_RECOVERY | NORMAL_BASELINE`，不能用一组 nullable plan/policy/reservation 字段猜分支。初始短事务先校验 request fingerprint并取得 session generation-claim mutex，再按下表冻结 Batch `requested_execution_kind`：

| execution kind | 入口条件 | 初始 Batch status | 权威执行租约/artifact |
|---|---|---|---|
| `PLAN_SLOT` | ENFORCE + ADVANCE/APPEND + active exact-next slot + 无 override | SELECTING_DIRECTOR | PlanSlot lease + CompiledPolicy(PLAN_SLOT) |
| `STAY_PATCH` | ENFORCE + 合法 STAY root/chain + 无 override | SELECTING_DIRECTOR | BatchReservation + CompiledPolicy(STAY_PATCH) |
| `SAFE_RECOVERY` | 原子消费 mode=SAFE_RECOVERY 的有效 override | SELECTING_RECOVERY | BatchReservation + BaselineRecoveryPolicy |
| `NORMAL_BASELINE` | 原子消费 NORMAL_BASELINE override，或无 plan/off/unsupported 的显式 baseline 路径 | BASELINE_SELECTING | generation claim + fresh normal snapshot；无 Director policy |

override 消费事务先复制 Gate provenance并执行 state revision `+1`；本例即 `122 → 123`。PLAN_SLOT 只 lease `next_slot_seq`，STAY/RECOVERY 创建 reservation；任一路径都不允许绕过 generation claim。随后事务外构建本分支所需 fresh CandidateSetRef/ConstraintSetRef、编译（如需要）并选片。

最终 `BEGIN IMMEDIATE` 先执行所有分支共有的检查：generation claim owner/fencing、Batch status/request fingerprint、artifact/ref/constraint TTL、candidate exact membership、最新 eligibility，以及 current eligibility/temporal/amplification/admission/ranker versions。再按 discriminator 执行：

- PLAN_SLOT：要求 global mode=ENFORCE，`runtime epoch == Plan == CompiledPolicy == Batch claim`，active plan/state revision/slot owner-request-token 全匹配，current taxonomy/recipe/guardrail/compiler versions 等于 policy；
- PLAN_SLOT_SUBSTITUTED（PLAN_SLOT 失败后的 final outcome）：仍要求 global mode=ENFORCE、原 Plan/slot/state revision/lease owner-request-token/TTL 全有效，且未被 Gate/PATCH expiry/replan/runtime epoch 撤销；禁止 CompiledPolicy/RecoveryPolicy 引用，以 fresh normal baseline ref 和当前 hard rules 整批重选，提交时才推进原 slot；
- STAY_PATCH：要求 global mode=ENFORCE/epoch 匹配，origin Plan 仍 ACTIVE/DELIVERED 且未 invalidated，root/latest patch chain、patch sequence 和 reservation owner/token/TTL 全匹配；current taxonomy/recipe/guardrail/compiler versions 等于 policy；不读取或推进 next slot；
- SAFE_RECOVERY：不要求 active Plan 或 CompiledPolicy；要求 global mode=ENFORCE/epoch 匹配，Batch consumed Gate ID/mode/validity 与 RecoveryPolicy 完全相等，expected state revision=override consumption 后 revision，profile revision/view digest、taxonomy/anchor set、candidate/constraint refs、reservation owner/token/TTL 和全部 policy versions 匹配；
- NORMAL_BASELINE（无槽）：禁止仍有效的 Plan/slot lease、CompiledPolicy/RecoveryPolicy 和 reservation；若由 override 进入，Batch consumed provenance 必须解析到已 APPLIED Gate 且证明 claim 时未过期。已撤销旧 Plan 的 ID 只可留在 failure provenance，不能作为 execution binding。最终只按当前 baseline loader 与 hard rules 提交，不要求 Director epoch 继续相同。

对权威 survivor set 在事务内再次执行相应 verifier，不静默删选或换 item。成功提交的原子写集合为：

- PLAN_SLOT：recommendations + server rank、pool shown、Batch COMMITTED(`PLAN_SLOT`)、Slot COMMITTED、session cursor、generation claim terminal，以及 `BATCH_COMMITTED + SLOT_COMMITTED`；
- STAY_PATCH：recommendations + shown、Batch COMMITTED(`STAY_PATCH`)、reservation/generation claim terminal 与 `BATCH_COMMITTED`；origin slot/cursor 不变；
- SAFE_RECOVERY：recommendations + shown、Batch COMMITTED(`SAFE_RECOVERY`)、reservation/generation claim terminal 与 `BATCH_COMMITTED`；plan/slot/cursor 全为空/不变；
- PLAN_SLOT_SUBSTITUTED：recommendations + shown、Batch COMMITTED(`PLAN_SLOT_SUBSTITUTED`)、Slot SUBSTITUTED、session cursor、generation claim terminal 与 `BATCH_COMMITTED + FALLBACK_SERVED + SLOT_SUBSTITUTED`；
- NORMAL_BASELINE：recommendations + shown、Batch COMMITTED(`NORMAL_BASELINE`)、generation claim terminal 与 `BATCH_COMMITTED/FALLBACK_SERVED`；plan/slot/cursor 全为空/不变。

上述每个 Director-aware 成功提交事务还必须把 `DirectorSessionState.current_batch_instance_id` 指向本次 Batch，并恰好递增 `state_revision +1`；没有 slot 的 baseline/recovery/STAY 也不例外。CompiledPolicy/RecoveryPolicy 的 expected state revision 绑定提交前值，最终 CAS 成功后才产生新值。Legacy 路径没有 DirectorSessionState，不作此写入。Batch 的三个 committed discriminator、Slot outcome、`BATCH_COMMITTED` detail 与 presentation ack 分支必须按 17.8 逐值相等。

失败分支也锁定：PLAN_SLOT 只有在原 slot lease 仍有效且失败属于本次 compile/select/verify shortage 时，才能直接 `SELECTING_DIRECTOR → BASELINE_SELECTING`，用 fresh normal snapshot 成功提交后把 slot SUBSTITUTED；若 lease 被 REPLAN/supersede/PATCH expiry/runtime epoch 撤销，则转 BASELINE_FALLBACK_PENDING，后续 no-slot baseline。STAY 失效清 reservation后走 no-slot fallback或返回其显式合同错误，不触碰 origin slot。SAFE_RECOVERY 失效/失败则取消 reservation、`SELECTING_RECOVERY → BASELINE_SELECTING`，用相同 Batch/generation claim 走 normal baseline；baseline 也失败才 FAILED。任何 Director/recovery tentative rows 都先完整丢弃。

presentation ack 在独立幂等事务按 committed execution kind 分支：PLAN_SLOT 或 PLAN_SLOT_SUBSTITUTED batch 更新关联 batch/slot/recommendations，并在 slot 首次进入 PRESENTED 时追加 `SLOT_PRESENTED`；STAY_PATCH、SAFE_RECOVERY 与 NORMAL_BASELINE 只更新新 batch/recommendations，绝不改 origin/next slot；每次合法 ack 都追加 `BATCH_PRESENTED`，状态与事件同事务。

若现有 DAO 暂时无法让这些表同事务提交，该 PR 不得进入 enforce；outbox 只能用于非权威异步派生，不可代替 slot fencing。

### 18.4 Shadow 隔离

- shadow 可以读取同一 snapshot 并运行 selector，但不得调用 `persist_pool_serve_async()`；
- shadow 不写 recommendation、不标 shown、不占 slot、不触发 discovery，不改变 session overlay；
- 只记录 proposal、可编译性、虚拟 realized mix、延迟、成本和护栏结果；
- SHADOW_VALIDATED 后 compiler/evaluator 失败只把 ShadowEnvelope 置 FAILED 并写 `SHADOW_EVALUATION_FAILED(shadow_failure.v1)`；不使用 ENFORCE 的 PLAN_FAILED cleanup，也不改任何 control-plane revision；
- shadow 结果不能产生伪 reward，也不能拿预测 CTR 声称体验提升。

### 18.5 异步任务所有权

- 每个 StrategyStream 一个 single-flight planner；
- 后到强反馈提升 desired version，不并发启动多个 LLM；
- shutdown 时取消未提交 planner，已激活 plan 不受影响；
- restart 后从数据库恢复 active plan、state revision、未关闭 feedback window；只有仍属 active plan 且未被 supersede 的过期 LEASED slot 才以 CAS 回到 PENDING，并在下次 lease 时取得更大的 fencing token；已被新 plan revoke/supersede 的 slot 永远保持 REVOKED/CANCELED；
- 过期的 SELECTING_DIRECTOR、SELECTING_RECOVERY、BASELINE_FALLBACK_PENDING 或 BASELINE_SELECTING claim 由 janitor 在同一事务标为终态 `FAILED/REQUEST_CLAIM_EXPIRED`，并在仍匹配时释放 slot 或取消 reservation、终结 generation claim；之后新 request 才能开始生成。旧 request 日后重试只返回其终态失败，不能借原 ID 消费新的 slot/override；
- 不从内存 `_last_served_bvids` 推断 plan 进度。

### 18.6 Runtime epoch 与 kill switch

`director.mode` 的线性化点是 `recommendation_director_runtime` 行提交。切到 off 或改变 execution config 时，事务先递增 epoch；此后任何 request claim、Plan activation、lease、Gate、compile 和 final commit 都必须读取新行，旧 epoch 立即失败。若一个 Director commit 先拿到 SQLite 写锁并完成，则它在线性化点之前合法；kill switch 先提交则旧 worker 不可能再提交。

OFF 不要求在同一全局事务扫描全部 session，但必须同时写 durable cleanup outbox。janitor 与每次后续 session 请求都执行幂等 fenced cleanup：把旧 epoch ACTIVE/DELIVERED Plan 置 EXPIRED(reason=`DIRECTOR_DISABLED` 或 `RUNTIME_EPOCH_CHANGED`)，close open batches、cancel PENDING、revoke LEASED、清 active pointer/overlay/override，并写完整 lifecycle events。旧 epoch SELECTING_DIRECTOR 若 session 与 request fallback deadline 仍有效，原子转 BASELINE_FALLBACK_PENDING、清 Director binding并写 event；旧 epoch SELECTING_RECOVERY 则取消 recovery reservation并转 BASELINE_SELECTING，保留 consumed-override provenance。原 owner/重试/janitor 以更高 batch/generation fencing 使用 fresh normal snapshot 提交同一 batch ID；只有 session 已关闭或 fallback deadline 到期才置 FAILED。已 committed/presented rows 不回滚。重新开启 enforce 会再次递增 epoch；即使 cleanup 尚未扫完，旧 Plan/Policy 因 epoch 不同也永不可 lease/commit，只有当前 epoch 的新 DirectorInput/Plan 可以执行。

## 19. 失败与回退矩阵

| 故障 | 当前请求行为 | 后台行为 | 记录 |
|------|--------------|----------|------|
| 无 active plan | baseline serve | 异步 plan | `NO_ACTIVE_PLAN` |
| LLM timeout/401/invalid JSON | baseline，不重试热路径 | 有界退避 | provider/error code |
| schema/semantic invalid | baseline | 可再请求一次新 plan，不修 prompt 文本 | validation errors |
| plan TTL 到期 | baseline | replan | `PLAN_EXPIRED` |
| SERVING ref 过期 | 重取并重编译一次；仍失败则 baseline | 无 | `CANDIDATE_SET_EXPIRED` |
| topic lane 缺货 | PATCH | 可选 supply advisory | moved slots |
| hard guard 导致不足 | Director 各 lane minimum 仍满足则较短；任一 minimum 失败则纯 baseline substitution；普通 baseline 保持现有较短语义 | refill | `UNDERFILLED_AFTER_HARD_GUARDS` / `SLOT_SUBSTITUTED` |
| 强反馈与旧计划冲突 | recovery/baseline | replan | suppression + reason |
| lease/CAS/fencing 冲突 | 幂等 winner 返回；loser 放弃 | 无 | stale token/revision |
| FinalPolicyVerifier 失败 | 丢弃 Director rows，用 fresh normal snapshot 做纯 baseline substitution；禁止递归 fallback | 修复/报警 | violation codes |
| HTTP 响应丢失 | 重试返回原 committed batch | 无 | same request_id |
| presentation ack 缺失 | 不计曝光、不触发方向反馈 | TTL 标 unknown | `PRESENTATION_UNKNOWN` |
| DecisionLog 写失败 | enforce 模式 fail closed 到 baseline；不得无日志实验 | 修复/报警 | `AUDIT_WRITE_FAILED` |
| side-effecting GET top-up | baseline top-up | 不计划、不推进 slot | `BASELINE_TOPUP` |
| Director 全局关闭/runtime epoch 改变 | 旧 Director 结果丢弃，当前请求无 slot baseline；legacy 语义兼容 | fenced/lazy cleanup 旧 Plan/lease | `DIRECTOR_DISABLED` / `RUNTIME_EPOCH_CHANGED` |

关键原则：Director 的失败不能让推荐失败；但如果 enforce 的决策无法可靠留痕，则该次 Director 策略不能悄悄执行。

## 20. 安全、隐私与 prompt 注入

1. Director 输入默认只含本地聚合画像、受控 ID 和统计，不含 Cookie、账号、URL 或完整浏览历史。
2. 候选内容不进入 prompt；上游生成的 topic 文本也必须先 taxonomy mapping，不能作为 executable token，从而显著缩小内容注入面。
3. v1 只传 IntentProjector 的结构化结果，不传原始 feedback note。未来确需原文时必须独立定界、标 provenance、限长，并且永远只是 data。
4. Planner system prompt 字节稳定，per-call 数据只进入 user payload，保持现有 prompt-cache 约定。
5. 输出必须走 strict JSON schema、allowlist 和长度限制；未知字段拒绝。只有明确声明可 clamp 的软数值才能 clamp，其余整份拒绝。
6. Supply brief 只含受控 ID 和 reason code，不能含 URL、命令、凭证或任意搜索 query；Discovery Planner 自己生成受控 query。
7. expression 只能引用 evidence code，不能凭空说“你喜欢/讨厌 X”，不能暴露敏感画像。
8. DecisionLog 不存原始 prompt、chain-of-thought、完整 profile 或 feedback note；保存 fingerprint、digest、code 和 opaque refs，并执行 90 天明细 retention。
9. 远程模型模式必须可见、可关闭；用户可选择现有本地 provider。关闭 Director 不影响 baseline 推荐。
10. 不从推荐行为推断或暴露敏感属性；Director 只使用 Soul 已允许进入推荐的字段。
11. 所有远程 LLM 调用服从现有 provider 路由、超时、日志脱敏和用户配置。

## 21. 配置草案

建议新增独立顶层 `[director]`，因为它是跨 recommendation、discovery、feedback 的策略模块，而非某个 LLM route：

```toml
[director]
mode = "off"                     # off | shadow | enforce
planning_horizon = 3              # v1 固定为 3
commit_horizon = 1                # v1 固定为 1
plan_ttl_seconds = 1200
planning_candidate_ref_ttl_seconds = 1200
serving_candidate_ref_ttl_seconds = 90
compiled_policy_ttl_seconds = 90
feed_session_ttl_seconds = 1800
slot_lease_seconds = 30
min_compilation_commit_budget_seconds = 10
presentation_ack_timeout_seconds = 120
presentation_clock_skew_seconds = 300
presentation_late_ack_seconds = 600
batch_feedback_window_seconds = 600
replan_cooldown_seconds = 60
max_plan_calls_per_session = 4
planner_timeout_seconds = 20
temporary_suppression_ttl_seconds = 1800
max_stay_patch_batches_per_slot = 3
shadow_sample_rate = 1.0
enforce_sample_rate = 0.0
enable_expression = false
enable_supply_advisory = false
audit_required_for_enforce = true
```

配置原则：

- 默认 `off`，确保升级无行为变化；
- `shadow` 生成和编译计划，但用户看到 baseline；
- `enforce` 也必须有 runtime kill switch；
- v1 固定 horizon/commit 值，配置出现其它值应拒绝启动而不是静默接受；
- recipe 参数不暴露给 LLM，也不在普通设置页提供自由编辑；
- 以后 bandit、critic、权重 delta 使用独立版本与开关，不能偷偷加入 v1。

## 22. Prompt 与模型策略

### 22.1 Prompt 结构

System：

- 定义 Director 职责与禁止项；
- 给出固定 schema 与枚举语义；
- 强调只使用 supplied IDs；
- 明确没有候选选择权；
- 明确只能返回 `DirectorProposal`，不能生成正式 plan 字段；
- 明确 rationale 只能使用 codes/refs。

User payload：

- deterministic canonical JSON；
- `sort_keys=true`；
- compact separators；
- profile/session/capability/current-plan 分块；
- 原始用户文本和外部内容不进入 payload；
- v1 先不做动态 few-shot，避免把历史脏数据带回 prompt。

### 22.2 调用策略

- 异步 single-flight；
- temperature 低到中等，依靠受控动作空间而非自由创作；
- 最多一次 strict-schema format retry，且 retry 永不进入 serve 热路径；
- system prompt 字节稳定以复用现有 prompt cache；不能把旧 Proposal 当当前 Plan 缓存命中；
- provider fallback 只在总调用预算内发生。
- 远程 provider 不可用时直接 baseline；不为了 Director 拉长用户请求超时。

### 22.3 User Twin / alignment critic

不进入 v1 决策链。后续最多作为 shadow 诊断器：

1. Planner 产生 3 个受控计划 proposal；
2. critic 只基于同一 compact context 打分；
3. validator 先过滤非法/不可执行计划；
4. critic 结果先 shadow；
5. 只有其评分与真实随机实验反馈长期稳定校准后，才讨论 plan-level rerank。

critic 不看候选 item，不允许绕过 validator，也不允许同时改 proposal、改权重和定义 reward，避免两个相关模型互相证明。

## 23. 评估与决策日志

### 23.1 不先造一个可被游戏的总分

v1 保存指标向量，不用未经校准的单一 reward 自动学习：

**单 batch：**

- relevance 分布；
- topic/style/source diversity；
- planned/realized lane allocation 与 relaxation；
- 显式 like/save/dislike/dismiss；
- inferred satisfaction；
- underfill、fallback、repair；
- presentation unknown、继续/退出和同方向 reshuffle。

**工程与成本：**

- Proposal accept/stale rate、compile/degradation/fallback rate；
- Planner calls/tokens/cache hit/成本每 FeedSession；
- plan、compile、select、commit、presentation ack 各阶段延迟；
- lease contention、幂等命中、audit failure 和 baseline fallback 原因。

**连续三 slot：**

- 每个 slot 的 reach rate 与实际走完数量；
- role transition adherence；
- 主题连贯性和主题单调性同时报告；
- explore lane 的接受/拒绝率；
- 强反馈后的恢复 batch 数；
- 重复和护栏违规。

**跨会话：**

- 回访间隔和回访率代理；
- 被用户长期否定方向的复发率；
- 临时兴趣是否被错误固化；
- 用户对推荐理由/路径的显式认可。

CTR、观看时长或 session length 都不能单独作为总目标；防止通过标题党、重复熟悉内容或拖长会话获得虚假提升。

### 23.2 Shadow 能与不能验证什么

Shadow 可以验证 schema、可编译率、执行偏差、护栏、延迟、成本和 fallback；不能观察用户对未曝光批次的反事实反馈，不能用 User Twin 分数或预测 CTR 声称用户效果。

### 23.3 随机实验合同

- 因 shown/fatigue、现有 Soul 学习和共享候选池会跨 session carry over，主因果实验不把同一 profile 的 FeedSessions 当独立样本；使用预注册的 `PROFILE_TIME_BLOCK` blocked switchback（例如按日/固定 session block），block 间插入 baseline washout，所有 block 内 FeedSession 共享 assignment；
- assignment 在生成任何 Director 结果前完成并 sticky；分析以 profile-time cluster 为单位。单用户本地部署只能做 within-profile switchback/描述性结论，不能把 session 数当独立用户数，也不能由短实验声称长期效果；
- 主分析使用 intention-to-treat，不能只看实际到达 Slot 3 的幸存用户；
- 同时报告 Slot 1/2/3 reach rate，避免 survivor bias；
- reward window 固定，late feedback 和 retraction 按预注册规则处理；
- selection 和 expression 开启后使用 2×2 因子实验，拆开“编排变好”和“文案变好”；该主实验期间 `enable_supply_advisory=false`，避免第三个会改变共享池的处理；
- supply advisory 只能在独立 profile-time cluster 实验中评估，或样本足够时预注册 2×2×2 设计与更长 washout；不能把它暗中并入 selection treatment；
- 用户效果指标、guardrail、最小样本和停止规则在看结果前预注册。

### 23.4 必须先记录的实验字段

从 shadow 第一日记录：

```text
planning input_digest + profile/session policy revisions and digests
eligible actions
assignment action + probability
strategy action + probability (future bandit only)
plan/policy/recipe/model/prompt versions
candidate member/feature/inventory digests
batch ID；item/server-rank/PlanLane attribution 通过 recommendation rows 关联，actual rendered position 通过 presentation items 关联
presented/closed timestamps and per-slot reach
repair/fallback codes
reward events + windows
```

没有 propensity 的旧日志不能伪装成可做 IPW/DR 的随机实验数据。

### 23.5 v1 工程验收门

所有分母由 `engineering-denominator.v1` 版本化：eligible compile attempt 指合法且未 stale 的 Plan、受支持 limit/scope、fresh normal baseline 在最新 hard guards 后至少有 `requested_limit` 个 unique items、SERVING/Constraint refs 完整且没有 provider/DB 基础设施故障；不得因某个 lane 难、compiler 返回 shortfall 或最终 fallback 而把该样本移出分母。每次 DecisionLog 保存 `denominator_eligible + exclusion_code`，exclusion codes 使用闭合 registry并单独报告占比。

另有不可事后缩小的上游分母 `eligible_planning_episode`：所有 mode=SHADOW/ENFORCE、FeedSession OPEN、受支持 surface/scope、runtime epoch 有效的 durable planning episode，从 17.3 outbox 首次入队时计数；同一 episode 的合并 trigger refs 不重复计数。它包含后来 provider 失败、schema reject、semantic reject、因同 session batch 或弱 Gate context commit 而 stale、budget 耗尽或被持续请求推迟的 episode；仅用户关闭 session、明确切 off、实验未分配 Director 等事前定义条件可排除，并记录 exclusion code。`fresh accepted Plan / eligible_planning_episode`、reject/stale、超时和 context-churn 分别报告。每次阶段晋级的窗口至少有 50 个 eligible planning episodes 和 100 个 eligible compile attempts；任一分母为零或低于样本下限时结果为 `NOT_EVALUABLE`，不得把空集合当 100% 通过。数值门槛是 v1 工程默认值，任何修改需先升级 denominator/acceptance registry 并在观察结果前登记。

Allocation 同时报两种：ITT adherence 把所有 assigned Director attempts（含 substitution/failure）纳入；conditional adherence 只看 CompiledPolicy 在 exact ref 上标 `compile_status=EXACT` 的 target positions，分子是最终 rows 正确命中 planned lane 的位置数。不能用“有足够候选”做事后人工筛选。

| 指标 | 进入下一阶段的门槛 |
|------|--------------------|
| hard guard violation | 0 |
| schema + semantic validation determinism | 相同输入 100% 相同结果 |
| planning episode terminalization | 100% 在 `accept_until + 30s` 内形成 typed accepted/rejected/stale/failed/deferred 终态；超时单列 |
| fresh accepted Plan coverage（全部 eligible planning episodes） | ≥ 80%；provider/schema/semantic/stale/预算耗尽均留在分母 |
| proposal reject + stale rate（全部 eligible planning episodes） | ≤ 20%；context-churn stale 单列 |
| valid plan compile rate（engineering-denominator.v1） | ≥ 95%；全部 exclusion 同报 |
| Director ITT whole-batch substitution/fallback | Phase 1B 报 virtual 值，Phase 2 起实际值 ≤ 10%；所有 assigned Director attempts 入分母 |
| conditional allocation adherence / ITT adherence | conditional ≥ 90%；ITT 不设隐藏分母并持续报告 |
| duplicate shown caused by Director | 0 |
| same request_id commits multiple batches | 0 |
| superseded/canceled slot committed | 0 |
| background GET/restore/prefetch advances slot | 0 |
| unpresented batch used for direction reward | 0 |
| Director LLM calls on serve hot path | 0 |
| pure compiler CPU P95 | ≤ 20 ms；从 typed refs 已解析到 policy artifact，含 repair/trace，不含 selector |
| Director prepare P95 | ≤ 100 ms；从 request claim 前开始，含 DB 等待、targeted snapshot、Candidate/Constraint refs、compiler 和 policy persist，不含既有 selector/LLM |
| Gate create+apply P95 | ≤ 20 ms；含 feedback queue claim、Gate DAO、事务等待、state/slot/event/outbox 写入 |
| enforce 决策缺 audit | 0 |
| fallback behavior | 与当前 baseline 接口兼容 |

用户效果门槛必须在 baseline 日志建立后预注册，不能看完结果再挑指标。

## 24. Shadow、上线与回滚

### Phase 0A：先修 batch 生命周期，Director 仍关闭

实现：

- FeedSession 与 batch_intent；
- `recommendation_batches` 和 request idempotency；
- side-effecting GET 明确映射为 baseline top-up；
- presentation ack 接通已有 `presented`；
- Director-aware click/like/save/dismiss 写入携带 `recommendation_id + batch_instance_id` 的 durable EventIngress/feedback queue 桥，并完成 behavior-before-ack 的等待归因；即使 Phase 0A 尚不开 Gate，也要保存可追溯事件；
- 多 tab/scope 隔离和 fenced commit 骨架。

退出条件：`mode=off` 行为兼容，后台 GET、恢复、prefetch 均不会消费 slot，且呈现/点击/赞踩/收藏/忽略都能以 batch 和 recommendation 身份持久追溯。Phase 2 enforce 不得早于此桥完成。

### Phase 0B：taxonomy、合同与 replay harness

- topic taxonomy / adjacency；
- immutable DTO/schema、Recipe Registry、Proposal/Plan validator、typed DecisionLog；
- reason-code registry 与历史 replay fixture；
- 不调用 LLM，不改变用户结果。

退出条件：合同、属性测试和 fallback 测试通过。

### Phase 1A：Candidate/Compiler shadow

- CandidateSetRef persistence、ConstraintSetRef 与 capability cube；
- targeted Candidate Bus、Compiler、Constrained Composer 和 FinalPolicyVerifier；
- 只使用 fixture/static plan 跑 replay 与 shadow，不调用 Planner；
- shadow 零业务写入。

退出条件：exact-ref binding、约束分配、final verification、fallback 和并发测试通过。

### Phase 1B：Planner shadow

- `mode=shadow`；
- Planner 异步生成三-slot Proposal；
- 编译 shadow policy 并运行 shadow selector；
- 用户仍看到 baseline；
- shadow 不写 recommendation/shown/session overlay，不触发 supply；
- 比较可执行率、护栏、分配、延迟和成本，不评价用户喜好。

退出条件：工程验收门全部通过，且无隐私/日志问题。

### Phase 2：单-slot 随机 enforce

- 每个 FeedSession 只 enforce 第一个 Director PlanSlot；同 session 后续请求强制 baseline，不等价于提前上线三-slot；
- assignment 与 PlanEnvelope 固化 `execution_horizon_limit=1`；第一个 slot commit/substitution 的同一事务 cancel 其余 PENDING slots（reason=`ROLLOUT_SLOT_LIMIT`）、将 Plan 标 `COMPLETED`、清 active pointer 并置 session `director_rollout_exhausted=true`。该 session 后续始终 baseline，不触发新 Director Plan；升级 Phase 3 只影响新 assignment/session，旧 Plan 绝不复活；
- recipe/topic lane 目标生效；
- 不启用反馈 replan；
- 按预注册 PROFILE_TIME_BLOCK switchback 分流 baseline/director，FeedSession 读取 sticky assignment，记录 probability 并做 cluster-level ITT；
- kill switch 可立即回 off。

退出条件：负反馈和退出等 guardrail 不劣于预注册阈值。

### Phase 3：滚动三-slot + Feedback Gate

- 启用 remaining intents；
- 开启 CONTINUE/PATCH/REPLAN；
- 强反馈旧方向 suppression；
- 验证恢复速度和 replan 成本。

### Phase 4：Expression 与 supply brief

- expression intent 接入现有文案，并与选片做 2×2 实验；
- discovery 只消费受控 existing-topic advisory；
- 自由文本 `new_direction` 仍不进入 enforce。

### Phase 5：可学习策略臂

- 只在 recipe/topic exploration 等小动作空间引入 contextual bandit；
- 记录真实 action probability；
- 只有具备正确 propensity 和 overlap 后才使用 DM/IPW/DR 等估计；
- 仍不让 bandit 直接从全部视频挑 item。

### 回滚

任一阶段回滚只需：

```text
director.mode = "off"
```

runtime manager 必须把这次切换持久化并递增 `director_runtime_epoch`；不能只改进程内布尔值。历史 plan/log 保留只读，旧 epoch lease 立即失效并按 18.6 清理；再次开启必须重新规划。现有 recommendation engine 的无 policy 路径必须持续被回归测试，不能在 Director 上线后腐化。

## 25. 测试策略

### 25.1 单元测试

- schema 拒绝未知字段、非法 enum、越界长度和错误配额；
- canonical JSON/digest 稳定；
- recipe 缩放的 floor-min/ceil-max/Hamilton target、tie-break 与 limit 1..20 边界；
- shortage repair 顺序；
- hard guard 永远不可关闭；
- Gate truth table；
- `director-gate.v1` 的 confidence、10/15 分钟窗口、同 PlanLane 不同 item 去重、同 item like+save 只算一次、hysteresis 与并列优先级；
- feedback lane attribution 与 IntentProjector target/scope；
- PlanLane 完整 identity，禁止裸 lane_key 跨 slot 聚合；
- plan TTL/revision/basis 判断；
- request fingerprint 覆盖 client/surface/mode/previous/restore/assignment，request/gate/planner 幂等；
- Proposal 不能设置 plan ID、状态、role、hard filter 或候选 ID；
- recipe/role/expression compatibility、rationale typed evidence predicate 和 taxonomy adjacency；
- valid style enum 与 unknown content form 行为。
- strict lifecycle/correction/PLANNING_DEFERRED DecisionLog union、重复字段 equality validator 与 idempotency projection；
- `SESSION_INTENT_UPDATED/SESSION_SUPPRESSION_EXPIRED/PROFILE_VIEW_UPDATED` 的 revision/digest 与状态同事务相等；Batch requested/committed kind、outcome、policy/ref XOR；
- 机械约束：时间、数组唯一/长度、prefer/avoid 互斥、capability truncated/count、digest/ID pattern；
- runtime mode/epoch compatibility 和 Recovery frozen anchor-set binding。

### 25.2 属性测试

随机生成 pool counts、limits 和合法 plan，验证：

- slots 总和恒等于 limit 或明确 underfill；
- 不超过 availability；
- 不违反 min/max 与硬护栏；
- 不会把一个候选分配给多个 lane；
- 输出确定性；
- fallback 图无环；
- 对全部 limit 1..20，缩放后 `sum(min)<=limit<=sum(max)` 且 target 总和精确等于 limit；
- 任意错误输入不会抛出未处理异常进入 serve。

### 25.3 集成测试

- active plan → lease → compile → serve → recommendation/shown/batch/slot/event 原子提交；
- 相同 request_id 重试；
- 相同 request_id 更换 previous/client/restore 任一字段必冲突，不能重绑 slot/patch；
- 两个进程并发 advance 只有一个 fencing token 能提交；
- 不同 request 抢 exact-next slot 时 loser 不得跳到下一 slot；
- concurrent replan 不能让 superseded slot 漏提交；
- DELIVERED Plan 被最后一批反馈触发的新 Plan supersede 时，旧反馈窗口关闭且同 session 仍只有一个执行 pointer；
- Director 与 baseline 都失败时 `SLOT_LEASE_RELEASED` 原子释放 lease、终结 request 而不推进 cursor；失败事务由 janitor 超时接管；
- REPLAN、PATCH expiry 与 runtime epoch 抢占 SELECTING_DIRECTOR 时都转可接管的 BASELINE_FALLBACK_PENDING，不消费 slot；
- kill switch 与 final commit 并发按 runtime row 线性化；off→on 后旧 epoch Plan/Policy 永不可复活；
- guardrail/taxonomy/recipe/constraint policy 热更新发生在 compile 后时，旧 policy final commit 必败；
- GET top-up、restore、prefetch、STAY 不推进 slot；
- legacy GET 只能读 LEGACY_BASELINE partition；并发 GET 与 Director POST/HTTP 响应丢失不能取走 Director-aware rows；
- ADVANCE 关闭当前 open stack，APPEND 只按 batch deadline 关闭，session/Plan cleanup 关闭全部作用域 batches/slots 且保留 execution outcome；
- Phase 2 首 slot commit 后取消剩余 slots、关闭控制窗口并完成 Plan，late reward 不重开 Gate；
- presentation ack 幂等，未 ack 不能归因；
- behavior-before-ack、ack-vs-close、late ack 与 correction/retraction 保持窗口边界；
- shadow selector 零业务写入；
- feedback 到达时 planner 仍运行；
- stale LLM 回包被拒绝；
- session-start/horizon/replan 的 Input 在当前 generation claim terminal 且 READY feedback 处理后才冻结；baseline/recovery 或弱 Gate commit 与规划竞态只形成有界 context-churn retry，不耗尽正常 4 次预算且总 provider 调用不超过 6；
- restart 恢复 active plan；
- platform-scoped lane 不泄漏跨平台候选；
- 多 tab 默认不争同一个 session slot；
- DecisionLog 失败时 enforce fail closed；
- whole-baseline fallback 使用 fresh normal snapshot，绝不复用 Director lane-targeted ref；SAFE_RECOVERY 不随 live profile 漂移 anchor；
- CLI/OpenClaw 未支持 session 时保持 baseline；
- `mode=off` 时 legacy 推荐选择语义与既有 response schema 向后兼容；新 FeedSession/batch 字段只通过版本化 endpoint 或显式 negotiation 返回，不要求跨随机时间点逐字节相同。

### 25.4 Replay 与故障注入

- 在仍处 CandidateSet TTL/debug-sampling TTL 的 frozen snapshot，或版本化脱敏 fixture 上 replay baseline 与 shadow policy；不对已清理的历史候选声称 exact reselection；
- provider timeout、401、invalid JSON、partial JSON；
- DB busy/transaction failure；
- pool 在 plan 与 compile 之间变化；
- 强反馈连发和 feedback retraction；
- 空池、薄池、单 topic 池和单平台池；
- 单主题目标与现有 topic cap 冲突；
- `_ensure_accessible_entry` 替换后最终配额变化；
- 超长/恶意 user intent 和污染 topic label。

## 26. 推荐代码边界

建议新增：

```text
src/openbiliclaw/recommendation/director/
  models.py          # immutable DTOs / enums
  taxonomy.py        # stable topic IDs / aliases / adjacency
  recipes.py         # versioned, non-LLM registry
  context.py         # compact DirectorInput builder
  candidates.py      # CandidateSetRef / capability cube / exact snapshots
  planner.py         # async LLM call + schema parse
  validator.py       # schema + semantic + feasibility checks
  compiler.py        # PlanSlot → CompiledPolicy
  composer.py        # quota-aware wrapper + final verifier
  projector.py       # event → normalized session intent
  gate.py            # deterministic feedback decisions
  service.py         # single-flight orchestration and lifecycle

src/openbiliclaw/runtime/
  recommendation_director.py  # trigger/cursor/planner scheduler
```

现有模块只做窄接口改动：

- `recommendation/engine.py`：引入 immutable `ServeRequestContext`，把现有内部流程拆成 `load/filter → topic canonicalization/taxonomy + broad-bucket mapping → freeze SERVING ref → compile → curator/composer → verify → atomic commit`；CompiledPolicy 必须在同一 prepared snapshot 上通过内部 compiler hook 产生，禁止外部先对另一份池快照编译后再塞进 `_serve_with_result_unlocked()`；默认 policy/compiler hook 为 `None` 时保持 baseline；
- `storage/database.py::load_pool_serve_snapshot_async()`：提供 prepared snapshot 与按 lane 定向读取的窄 DAO；exact ref 只保存 `item_key + frozen execution features`；
- `persist_pool_serve_async()` 底层事务：一起提交 batch/slot/event；
- `llm/prompts.py`：集中保存 Director 静态 system prompt 与 user payload builder；Planner 必须调用 `complete_structured_task(..., caller="recommendation.director.plan", inject_core_memory=False)`，并在 `llm/concurrency.py` 登记该 maintenance caller；
- `runtime/event_ingress.py`：只作为 durable 事件入口，Director 使用独立 poller/cursor；它当前不是多订阅 bus，不能覆盖 API 已安装的 Soul `wake` callback，若以后需要即时唤醒只能由 runtime 做显式 fan-out；
- `soul/profile_views.py`：新增集中序列化的 Director compact view，禁止在 prompt builder 手写画像；
- `api/app.py`：FeedSession、batch intent、presentation ack；当前 GET top-up 显式 baseline；
- `api/runtime_context.py`：注入 planner/store/executor，并支持热重载；
- `cli.py` 与 `integrations/openclaw/`：v1 明确 baseline，后续接入时使用相同合同；
- discovery keyword/inspiration planner：Phase 4 异步消费受控 supply advisory；
- expression builder：Phase 4 可选 intent code，默认行为不变；
- `discovery/pool_snapshot.py` 在 Phase 4 前保持 discovery deficit/saturation 合同；Director cube 属于 `recommendation/director/candidates.py`，到 supply bridge 才增加窄适配；
- 新 prompt builder 必须登记到 `tests/test_llm_prompts.py`，保证 system prompt 静态、变量只在 user payload。

不建议：

- 把 Director 塞进 `AgentOrchestrator.process_feedback()` 的 TODO；
- 用一个巨大 `director.py` 同时承担 prompt、状态、数据库和选择；
- 复制标题、正文、封面等完整内容缓存；短 TTL 的 `item_key + frozen execution features` exact 执行快照是 CandidateSetRef 的必要组成，不在此禁令内；
- 让前端维护权威 plan cursor；
- 让 LLM 输出可执行 Python/SQL 或直接调用 discovery/recommendation 工具。

## 27. 实施切片

建议按可独立回滚的 PR 切分：

1. **Baseline lifecycle**：FeedSession、BatchInstance、request idempotency、presentation ack、click/like/save/dismiss 的 durable 身份事件桥、GET top-up 隔离。
2. **Contracts + taxonomy**：models、schemas、Recipe Registry、topic IDs/adjacency、validator、文档、测试。
3. **Candidate + persistence**：CandidateSetRef、plan/slot/policy/event/state 表。
4. **Compiler + constrained selector shadow**：capability cube、targeted Candidate Bus、final verifier、replay。
5. **Planner shadow**：LLM prompt、single-flight、TTL、strict Proposal、成本与错误观测。
6. **Single-slot enforce**：随机 assignment、fenced commit、kill switch。
7. **Feedback Gate**：IntentProjector、lane attribution、session overlay、滚动 plan 和 recovery。
8. **Expression + supply advisory**：因子实验和 discovery 受控补货。
9. **OPE/bandit research**：先保证 propensity/overlap；不属于 core v1。

每个 commit/PR 都必须按 `CLAUDE.md#documentation-requirements` 做范围审计并更新 `docs/changelog.md`；接口、存储、运行时和 LLM 改动分别同步 `docs/modules/recommendation.md`、`storage.md`、`api.md`、`runtime.md`、`llm.md` 与 `docs/profile-usage.md`。只有配置真的变化时才同步 `docs/config.md + config.example.toml`。Phase 0A 涉及三端客户端时，还必须同步 `docs/modules/extension.md`、`docs/mobile-web-spec.md`、`docs/diagrams/web-architecture.html` 及 `extension/popup/popup-api.js`、桌面 `web/desktop/assets/js/app.js`、移动 `web/js/api.js + views/recommend.js` 的合同/测试。涉及数据流时更新 `docs/architecture.md`、`docs/spec.md`、中英架构概览、README 和 recommendation HTML 架构图；CLI/OpenClaw 的 v1 baseline 排除也要在每个相关切片明确。本文档本身不代表功能已实现，不应提前把主架构图写成已上线。

## 28. v1 最终产品行为

Director core v1 开启且计划可用时：

1. 用户拿到的仍是现有推荐卡片和现有反馈操作；
2. 一个 batch 内部更像一个有目的的组合，而不是 top-N 截断；
3. 用户可以明确选择继续、同方向换内容、换方向、轻松一点或暂时不看某类内容；
4. 明确反馈会改变后面尚未 committed 的路径；
5. 单次弱反馈不会让系统性格突变；
6. 任何异常都回到用户已经熟悉的 baseline；
7. 客户端未支持新 session/presentation 合同时仍走 baseline；
8. 用户不需要知道底层 LLM 是否成功，除非主动查看调试/解释界面。

Phase 4 开启后，屏级导语可以解释“这一组为什么这样组织”，但不进行心理分析，不虚构用户偏好，也不改变选片结果。

最终希望形成的能力不是“AI 替你选十条”，而是：

> 推荐系统能够提出一段有意图的内容路径，在用户真实反应中不断校准，同时把具体选择、安全和执行留给可靠的推荐基础设施。

## 29. 本规格锁定的决策

以下不再留给实现阶段临时选择：

1. Director 是策略层，不是 item ranker。
2. 三-slot 滚动规划，只 lease/commit 一个 slot。
3. 后端权威对象是 BatchInstance；`COMMITTED != PRESENTED`。
4. Director 只运行在显式 FeedSession；GET top-up、restore、prefetch 不推进计划。
5. LLM 只生成不可信 Proposal；正式 Plan 和全部状态由服务器盖章。
6. 候选对象不进 prompt；规划看 capability cube，执行绑定 exact SERVING ref。
7. topic 使用版本化稳定 ID 和 adjacency；lane 必须跨多个 fine topic，不能违反现有 cap。
8. v1 只使用仓库合法 style；精确论文/案例控制等待 `content_form`。
9. v1 没有 source quota、自由新方向、contrast/close 或连续权重。
10. Recipe Registry 和现有 guardrail policy 由代码拥有，Director 不能放松。
11. IntentProjector 与 Gate 是确定性边界，不是第二个在线 LLM。
12. 明确区分长期画像与 session overlay；即时明确限制先同步生效。
13. Replan 只能取消 PENDING slot，不能改写 COMMITTED slot。
14. reshuffle 中性且默认同方向，不自动等于主题否定或计划推进。
15. Shadow 零业务写入，只验证工程；用户效果必须随机 A/B，并使用 ITT。
16. enforce 无 audit、fencing 或 final verification 时 fail closed 到 baseline。
17. 默认 off，始终保留并回归测试现有无 policy 路径。
