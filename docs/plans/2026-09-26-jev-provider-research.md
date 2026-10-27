# Jev provider：官方能力核实与推荐编排适配

核实日期：2026-09-26。状态：研究与设计依据；未调用付费 API，未在 OpenBiliClaw 数据上测量延迟、准确率或校准情况。

## 结论

用户所指项目是 TypeSafe AI 的 **Jev**，官方于 2026-09-15 发布的 System One 决策模型。它接受上下文和预定义问题，输出有限选项、概率或等级分数；不生成自由文本。[官方发布文章](https://typesafe.ai/blog/introducing-system-one-models-and-jev)

适合把它接成独立 `DecisionProvider`，供候选过滤、每次选择两张卡、检查并局部替换 Agent 预编排共用。这是基于以下接口能力提出的 OpenBiliClaw 设计判断，尚无证据表明 Jev 已验证我们这个中文跨内容源推荐场景。三种编排模式可以共享 provider，但需要分别评估。

## 已核实的调用合同

| 项目 | 官方合同 | 对接含义 |
| --- | --- | --- |
| HTTP | `POST https://api.typesafe.ai/v1/systemone`；Bearer key；`state`、`model`、`questions` | 不应当成 OpenAI Chat Completions 接口 |
| `state` | 字符串、JSON object 或 array；只有文本输入 | 视频、封面、音频需先转成文本元数据 |
| `noul` | 是/否判断，返回 `noul`，范围 `[0,1]` | 可询问候选是否违背当前意图、某个计划位置是否需要替换 |
| `choice` | 从 `criteria` 映射选一个 key；返回 `choice`、全量 `probabilities`、`confidence` | 可从服务端列出的合法 pair 中选 `pair_id`；最多 255 个选项 |
| `score` | `criteria` 是有序等级数组，2–10 级；返回概率加权 `score`、`legend`、`probabilities`、`confidence` | 可做语义适配程度评价；分数范围是 `[0, 等级数−1]`，不是天然 `[0,1]` |
| 问题 ID | 应答使用请求中的同一 key；该 key 不进入模型推理 | 每个问题的 `instructions` 必须明确指向待评估卡片/位置，不能只靠 `check_card_123` 名字 |

以上请求与返回类型见[官方 API](https://docs.typesafe.ai/api)、[Choice](https://docs.typesafe.ai/primitives/choice)、[Score](https://docs.typesafe.ai/primitives/score)；文本输入限制见[State](https://docs.typesafe.ai/concepts/state)。原语名称不是 `boolean` / `select`；内部通用接口可以抽象命名，但 adapter 必须正确映射。

`Choice` 是单选，概率代表当前候选集合下的相对判断；独立 `Noul` 判断允许所有候选都不合适。可以加明确的 `none` 选项，并单独检查适配性；不能把 Choice 的最大概率当作用户会点击的概率。[Choice](https://docs.typesafe.ai/primitives/choice)、[官方结构一致性限制](https://docs.typesafe.ai/model-jaggedness/jev-1.13)

`confidence` 是从分布计算的统计值，不是另一个独立预测；只有 Choice/Score 返回它，Noul 没有该字段。官方建议在自己的领域选阈值。我们的 provider 应保留原始分布、原语、模型版本和规则版本；不能共用一个跨任务阈值，也不能把 `confidence=0.9` 宣称为推荐准确率 90%。[Confidence](https://docs.typesafe.ai/confidence)

## 批处理、序列状态与缓存

同一请求的所有问题看到同一份 `state`，分别独立求值；官方建议把独立问题或条件分支的预判放进一个请求，最后由代码决定采用哪些答案。因此可以一次检查未来多屏的多个位置，但不能在同一请求中让第二个问题读取第一个问题刚产生的答案。[State](https://docs.typesafe.ai/concepts/state)、[Speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)

“两个两个拼成一屏”建议用应用维护的状态循环：

```text
当前用户意图 + 已实际曝光内容 + 本屏已选 pair + 剩余配额 + 合法 pair 候选
    → Jev 选择 pair_id
    → 代码检查与登记两张卡、更新本屏状态
    → 下一次 Jev 决策
```

这是应用层递归更新状态的方案。已读官方 API/SDK 没有 conversation ID、服务端隐状态续接或可引用的上下文缓存句柄合同；不能称为 Jev 自带 RNN 记忆。每次都应携带本次决策所需状态。也不能把“本屏已经选中”误当作“用户已经看过”，后者只能来自真实曝光记录。[API](https://docs.typesafe.ai/api)、[State](https://docs.typesafe.ai/concepts/state)

同次请求共享 state 与跨请求 prompt cache 是两回事。公开 JS SDK 的 `Usage` 只有 `input_tokens/output_tokens`，未提供 `cached_tokens`；官方仓库中的跨请求缓存提案仍是用户提出的 feature request。因此成本预算按重复输入计费，不预设缓存优惠或 provider 记忆。可以在应用侧缓存相同版本、相同内容和相同上下文的结果；用户反馈变化后必须换键。[SDK v0.6.0 类型](https://github.com/typesafe-ai/typesafe-sdk-js/blob/v0.6.0/src/types.ts)、[缓存功能请求](https://github.com/typesafe-ai/typesafe-sdk-js/issues/10)

## 限额、性能与 SDK

截至核实日，官方列出的稳定版本是 `jev-1.13.0`；`jev-latest` / `jev-preview` 都指向该版，alias 会变化，响应提供实际模型版本。当前标价为输入 `$0.042 / 百万 tokens`、输出免费；限额为 `250,000 tokens/s` 和 `1,200 requests/min`，官方明确表示可能动态调整。上下文有两层约束：整个请求 `state + 所有 questions ≤64k tokens`，且 `state + 最长 question ≤32k tokens`。评估和阈值需固定版本；实际配额在接入时重查。[Models](https://docs.typesafe.ai/models)

发布文章称端到端 70–500ms，并说明大部分测试来自美国西海岸、短输入展示对 Jev 有利，部分大幅提速数据来自其自建 workflow benchmark。它们是厂商报告，不是我们的中文候选池或上海网络的 p95。六张卡逐对选择会串行进行三次决策，整屏延迟需实测，不能直接承诺 100ms 出完整屏。[官方发布文章](https://typesafe.ai/blog/introducing-system-one-models-and-jev)

官方 SDK：Python 包 `typesafe-sdk`，导入 `typesafe_sdk`，支持同步与异步；TypeScript 包 `@typesafe-ai/sdk`。来源由官方文档直接链接到 `typesafe-ai` GitHub organization；不要用名称相似的社区站点替代官方 endpoint/合同。[Python 文档](https://docs.typesafe.ai/sdk/python)、[Python 源码](https://github.com/typesafe-ai/typesafe-sdk-python)、[JavaScript 文档](https://docs.typesafe.ai/sdk/javascript)、[JavaScript 源码](https://github.com/typesafe-ai/typesafe-sdk-js)

两套 SDK 都支持重试，但超时语义需要分开处理：Python `RetryPolicy.timeout` 描述总重试预算；JS v0.6.0 的 `RequestOptions.timeout` 是每次尝试，类型注释明确没有总重试预算。快路径应由我们设置一次决策的总 deadline，并以剩余屏预算决定是否重试；HTTP 429/529 要作为 provider 不可用路径处理。[Python RetryPolicy](https://docs.typesafe.ai/sdk/python/api/retries)、[JS v0.6.0 类型](https://github.com/typesafe-ai/typesafe-sdk-js/blob/v0.6.0/src/types.ts)、[API 错误](https://docs.typesafe.ai/api)

## 对三种编排与过滤的设计判断

1. **Jev 逐对编排**：代码先产生数量有限的合法 pair，并给出两张卡的语义摘要；Jev 选一个 pair，代码更新局部状态再进入下一步。不能一次独立问“左卡选谁”“右卡选谁”后假定它们自然去重或互补。pair 候选数、配额、去重、作者上限和已曝光排除由代码负责。
2. **Agent 预编排 + Jev 修正**：Agent 生成未来数屏的意图、位置角色、暂定卡和备选；Jev 一次并行检查各未提交位置，代码形成待修位置集合，然后按依赖顺序逐对替换。检查之后的新一轮选择能看到替换结果；已展示卡不做原位重排。全局主题、叙事或偏好假设失效时，应请求 Agent 重规划。
3. **纯 Agent 预编排**：编排路径不调用 Jev；如果用户单独启用 Jev 过滤，仍只在过滤阶段调用同一 provider。编排模式与过滤开关必须分开，便于对照实验和故障降级。
4. **候选过滤**：先执行确定性硬规则，再用独立 Noul/Score 检查少量候选的语义相关性、重复和当下适配性；低置信结果进入保留/降权/备用路径，不把它当作永久封禁。官方已有“召回后逐候选多问题筛选”和 BM25 后重排范例，证明接口形态可用，未证明推荐效果。[RAG passage 分类](https://docs.typesafe.ai/cookbooks/classifying_rag_passages)、[重排 cookbook](https://docs.typesafe.ai/cookbooks/rerank_typesafe)

## 需要实验回答的问题

- **中文与跨源理解**：官方说英语是主要训练语言，CJK 准确率目前较低。需要用真实中文标题、简介、短评、双语术语分别评估过滤误杀率、pair 互补性和修正效果。[Models](https://docs.typesafe.ai/models)
- **质量和注入鲁棒性**：官方明确承认无关长上下文、间接推理、精确算数和对抗性内容会出错；类型正确并不保证语义正确。只传精简且有来源边界的卡片资料，把算数和约束校验放在代码。[Jev 1.13 已知限制](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
- **校准与稳定性**：用留出样本分别评估过滤、pair 选择、修改判定；不能用一次厂商概率或通用 benchmark 代替本域验证。不同原语、候选集合和模型版本的阈值不可直接迁移。[Confidence](https://docs.typesafe.ai/confidence)、[结构一致性限制](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
- **端到端服务指标**：尚未实测本地网络 p50/p95、长请求时延、串行三步与批量审查的差异、限流行为、重试账单及故障率；公开文档也未给我们可依赖的问句数量硬上限、跨请求缓存 SLA 或推荐效果保证。先做影子评估再定上线门槛。

本研究只依据官方文档、官方发布文章和官方 SDK 源码；SDK issue 被明确标记为用户功能请求，不用作已交付能力的证据。
