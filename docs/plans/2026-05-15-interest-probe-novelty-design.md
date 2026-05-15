# Interest Probe Novelty Design

**Problem:** 兴趣探针已经有 active / cooldown 去重和体验轴多样性，但这些约束仍然偏局部。它会漏掉与现有画像细项重复、与近期探针语义换皮、或跨入口反复问同一类体验的问题。用户体感上会出现“你已经知道我喜欢这个了，为什么还在问”的重复探测。

**Observed Evidence:**

- `InterestSpeculator._generate()` 只把 `profile.interest.likes[*].domain` 作为 confirmed domains 传给 prompt，本地质量门也只丢弃与一级 domain 完全相等的候选。
- `ingest_seeds()` 只检查 active 和 cooldown，不检查现有画像里的 domain / specifics。
- runtime probe push 会写入 `probed_axes`，但 `MemoryManager.save_discovery_runtime_state()` / `load_discovery_runtime_state()` 没有持久化该字段。
- OpenClaw `get_next_probe()` 会读取 probe axis 历史参与选择，但返回 probe 后不记录本次 domain / axis，连续调用可能重复拿到同一个候选。
- `_select_diverse_candidates()` 只平衡本轮 LLM 返回的候选，没有把既有 active pool 的体验轴分布纳入选择。

**Goal:** 在不引入 embedding 成本和大规模 schema 迁移的前提下，把兴趣探针从“精确字符串短期去重”升级成“画像 + 探针生命周期 + 近期推送历史”的统一新颖性治理。

## Design Decision

引入一个本地 `ProbeNoveltyGuard`，集中处理 speculative probe 的重复性和基础丰富度判断。它收集并规范化以下覆盖面：

- 现有画像：`profile.interest.likes[*].domain` 与 `profile.interest.likes[*].specifics[*].name`
- 当前 active speculation：domain 与 specifics
- cooldown / rejected speculation：domain
- 近期 probe history：`discovery_runtime_state["probed_domains"]`
- 近期体验轴：`discovery_runtime_state["probed_axes"]`

第一版只使用本地字符串规范化和中文 bigram overlap，不接 embedding。目标是拦截明显同义换皮，而不是做高召回语义搜索。

## Novelty Rules

候选进入 active pool 前，按以下顺序过滤：

1. 空 domain 或低质量候选仍由现有 quality gate 丢弃。
2. 与 active / cooldown / probed domain 完全相同的 domain 丢弃。
3. 与画像 domain 完全相同的 domain 丢弃。
4. 与画像 specifics、active specifics 或已探测 domain 有明显中文 bigram 重叠的候选丢弃。
5. 如果候选的 specifics 全部都已被画像覆盖，则丢弃；如果只有部分重复，则保留候选但去掉重复 specifics。

这样允许沿已有主轴继续“钻进去”，但不会把已经明确在画像里的细项包装成新探针。

## Diversity Rules

入池选择不再只看本轮候选，而是把当前 active pool 当作已有选择。选择优先级：

- 优先补 active pool 中缺失的 `experience_mode`。
- 优先补 active pool 中缺失的 `entry_load`，尤其是 `light`。
- 同等情况下仍按 confidence / weight 排序。
- 如果候选不足，降级为现有选择逻辑，避免 speculative pool 卡死。

runtime push 和 OpenClaw `next-probe()` 继续共用 `choose_next_probe_candidate()`，但 selector 的输入会同时包含近期 domain 和 axis 历史。

## Runtime State

`discovery_runtime_state` 保留短期 probe 记忆：

- `probed_domains`: `{normalized_domain: iso_timestamp}`
- `probed_axes`: `{experience_mode|entry_load: iso_timestamp}`
- `probe_feedback_history`: 最近 100 条显式 probe 反馈记录，用于把用户已经拒绝或负向聊过的方向纳入后续去重

runtime push 和 OpenClaw `get_next_probe()` 都在成功返回/推送 probe 后写入这两类历史。历史窗口先沿用 `_PROBE_COOLDOWN_HOURS = 4`，不新增长期疲劳表；用户明确 reject 的长期抑制仍由 cooldown 负责。

## Feedback History Addendum

探针反馈需要补一个轻量长期记忆。`confirm` / `chat_positive` 主要依赖画像晋升来避免重复；`reject` / `chat_negative` 则写入 `probe_feedback_history`，后续生成、seed 注入、runtime push 和 OpenClaw next-probe 都把这些负向 domain / specifics 当作 novelty coverage。`chat_neutral` 只留审计记录，不参与硬过滤，避免把一次普通追问误判成不感兴趣。

首版不把“已推送但没回复”建模为负反馈，因为没有可靠的忽略语义；短期重复仍由 `probed_domains` / `probed_axes` 控制。

## Data Flow

LLM generation:

1. 构建 prompt 时继续提供 active / cooldown / confirmed main axes。
2. LLM 返回过采样候选。
3. `ProbeNoveltyGuard` 用画像、active、cooldown、probe history 做本地 novelty 过滤。
4. diverse selector 基于 current active pool + filtered candidates 选择入池。

PreferenceAnalyzer seeds:

1. `ingest_seeds()` 增加可选 profile / runtime coverage 输入。
2. seed 进入 active 前走同一套 novelty guard。
3. seed 缺少 specifics 时仍可进入，但不能与画像 domain 或近期 probed domain 重复。

Probe selection:

1. runtime push 和 OpenClaw 都读取 persisted runtime state。
2. `choose_next_probe_candidate()` 同时接收 `probed_domains` 和 `probed_axes`。
3. 同时读取 `probe_feedback_history`，跳过与负向反馈明显重复的 domain，并在同等验证压力下优先避开负向反馈过的体验轴。
4. 成功选择后记录本次 domain / axis。

## Testing

新增回归测试覆盖：

- 画像已有 specific 时，近似 domain / specific 候选会被过滤。
- seed 注入不会把已有画像方向加入 active。
- `probed_axes` 在 `MemoryManager` 中能 round-trip 到 JSON。
- `probe_feedback_history` 在 `MemoryManager` 中能 round-trip 到 JSON。
- 负向 probe 反馈会让后续相似候选被 `ProbeNoveltyGuard` 过滤。
- `/api/interest-probes/respond` 会把 confirm / reject / chat sentiment 记录到 probe feedback history。
- OpenClaw 连续 `get_next_probe()` 不重复返回同一个 domain，且记录 axis。
- active pool 已经偏向 `knowledge|heavy` 时，新入池优先补不同体验轴。

## Non-Goals

- 不引入 embedding semantic dedupe。
- 不做复杂长期探测疲劳模型；只记录显式反馈并用于保守避让。
- 不修改 speculation state 文件格式，除非现有字段无法表达必要历史。
- 不把已有主轴整体排除；主轴仍然是最重要的 lateral 探索来源。
