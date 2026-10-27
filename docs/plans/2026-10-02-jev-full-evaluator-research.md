# Jev 完整候选评估替代：官方能力与上下文边界

核实日期：2026-10-02。范围：TypeSafe 官方 Jev、官方 API 与 SDK；未使用密钥、未调用付费服务、未安装依赖。本文将官方接口事实与 OpenBiliClaw 的适配建议分开记录。

## 结论

**Jev 可以承接完整候选评估中的有限分类、评分和布尔判断，但不能原样替代任意字符串生成或视觉理解。** 当前官方模型仍是 `jev-1.13.0`。它的原生返回只有 `Choice`、`Score`、`Noul`，没有自由文本/任意 JSON Schema 生成类型；官方明确建议生成任务使用生成模型。HTTP 返回 JSON 是运输格式，不代表模型能创造任意 JSON 内容。[Models](https://docs.typesafe.ai/models)、[API](https://docs.typesafe.ai/api)、[Generation 限制](https://docs.typesafe.ai/model-jaggedness/jev-1.13)

因此，可切换的完整 evaluator 应当共享应用层输出合同，再由 Jev adapter 把评估拆成有明确答案空间的问题，将答案组合成 metadata。若合同要求自由理由、新主题命名、正文总结或图片证据，必须明确使用受控文本/抽取替代，或交给支持这些能力的后端；不能把缺失字段补空后宣称与原生成评估语义完全相同。这是本项目的工程判断。

## 官方原生能力

| 原语 | 原生输入与返回 | 硬边界 |
| --- | --- | --- |
| `choice` | `criteria` 是 option key → 描述的映射；返回一个 `choice`、各选项 `probabilities`、`confidence` | 单选；最多 255 个选项；输出 key 来自调用方 |
| `score` | `criteria` 是有序描述数组；返回等级分布的期望 `score`、`legend`、`probabilities`、`confidence` | 2–10 级；范围 `[0, n−1]`；不用于恢复精确数值 |
| `noul` | yes/no 问题；可附 `true/false` 描述；返回 `[0,1]` 的 `noul` | 概率，不是布尔值；不带 `confidence` 字段 |

原语结构与上限见[API](https://docs.typesafe.ai/api)、[Choice](https://docs.typesafe.ai/primitives/choice)、[Score](https://docs.typesafe.ai/primitives/score)、[Noul](https://docs.typesafe.ai/primitives/noul)。`confidence` 是从分布计算的统计量；阈值需要按领域评估，不等于评估正确率，更不能直接写入现有 `relevance_score` 后宣称已校准。[Confidence](https://docs.typesafe.ai/confidence)

`state` 接受文本、JSON object 或 array；`instructions` 和 option/level 描述也可放结构化 JSON。这里的 JSON 是输入上下文和预定义答案描述，未增加自由 JSON 生成能力。[State](https://docs.typesafe.ai/concepts/state)、[Advanced: structure](https://docs.typesafe.ai/primitives/advanced)

同一请求可以有多个问题，全部看到同一份 state，独立并行回答。问题 ID 只用于匹配结果，不进入推理；每题必须在 instructions 中明确指向候选和字段。不能让同次请求中后一个问题依赖前一个新答案，也不能假设多个独立字段天然满足联合一致性。[State](https://docs.typesafe.ai/concepts/state)、[Speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)、[API](https://docs.typesafe.ai/api)

## 上下文长度：必须同时满足两条约束

官方 `jev-1.13.0` 模型页给出的预算是：

```text
T(state) + Σ T(question_i)       ≤ 64k tokens / request
T(state) + max T(question_i)     ≤ 32k tokens
```

这里的 question 包含 instructions、criteria 和相关上下文，不只是问题的一行文字。shared state 一次输入、各题并行求值；不能只检查 64k 总量，也不能把它解释为可以放 64k 正文。以上按官方 `64k/32k` 记法记录，文档未在该页明确 k 的精确整数展开值。[Models](https://docs.typesafe.ai/models)

官方文档没有列出独立的“每请求最多 N 个 questions”硬上限；不能把第三方 gateway 的问题数量限额当作 TypeSafe 原生限制。单项 Choice 的选项上限与整个批次的问题数是不同约束。[API](https://docs.typesafe.ai/api)、[Models](https://docs.typesafe.ai/models)

**本地精确 tokenizer：未找到官方发布物或计数接口。** 本次检查了[官方文档索引](https://docs.typesafe.ai/llms.txt)、[API](https://docs.typesafe.ai/api)、[Python SDK](https://github.com/typesafe-ai/typesafe-sdk-python) 与[SDK 返回类型](https://docs.typesafe.ai/sdk/python/api/types/responses)，未找到官方 `tokenize/count_tokens` 方法、tokenizer 文件或用于精确复现模型输入序列化的计数合同。API 提供调用后的 `usage.input_tokens/output_tokens`。这是截至核实日的发现范围，不是对未来能力的断言。

官方 Python SDK 当前 main 的版本声明为 `0.7.2`，依赖为 HTTP、Pydantic 和重试等库，没有 tokenizer 依赖；这与“未公开本地计数能力”一致，但不能独立证明内部 tokenization 细节。[官方 pyproject.toml](https://github.com/typesafe-ai/typesafe-sdk-python/blob/main/pyproject.toml)

本项目预算建议（推论）：

1. 将 token 估计、序列化字节数、官方响应 usage 分开记录；不能把字符数/字节数/别家 tokenizer 的结果标为 Jev 精确 token 数。
2. 同时估计整个请求与 state 加最长问题，保留 headroom；按候选拆批优先于截断单候选的重要证据。
3. 保存估计误差，后续用真实 usage 做校准；遇到明确的上下文超限错误时有界缩批或转给容量合适的 evaluator。
4. 单候选也超限时，明确报告无法满足当前输入合同并回退或终止本次评估；不能收到超长拒绝后临时删掉画像、负偏好或其他必需上下文继续，并把结果当作同一评估合同。若另设压缩/分块评估模式，需事先定义信息保留规则、单独版本化及记录 evidence provenance，不能伪装成一次完整正文评估。
5. 合并多个字段不会消除其 instructions/criteria token 成本；字段数、答案词表和长画像都会压缩每批可容纳的候选数。

## 图像、多模态与预算

官方明确标注 **text only**，不接收 image/audio/video。非文本输入应先转换为文本或结构化字段；当前没有官方 image token 预算、分辨率档位或视觉参数合同。把图片 URL 或 base64 写进 state 只是文字，不等于图片已被理解。[Models](https://docs.typesafe.ai/models)、[State](https://docs.typesafe.ai/concepts/state)

对 OpenBiliClaw 的推论：使用封面/关键帧视觉证据的评估不能透明切换成 Jev 原生视觉评估。可选路径是由独立视觉模型先产生可溯源描述，再把描述作为 Jev 文本输入；其成本和延迟单独计算。若未做视觉预处理，必须明确使用文本评估或回退到支持图片的 evaluator，不能悄悄忽略图像。

## 一次调用能否完成现有候选 metadata

仓库现有评估示例含 `score/reason/topic_group/style_key/franchise_key`，以及时效分类与原子证据组；见 [prompts.py](../../src/openbiliclaw/llm/prompts.py) 和[发现模块的时效合同](../modules/discovery.md)。下表是适配判断，不是 TypeSafe 对本项目的效果保证。

| metadata 类型 | Jev 一次调用中的表达 | 需要应用补齐的部分 |
| --- | --- | --- |
| 相关性/质量/风格程度 | 一个或多个 Score | 明确评分 rubric；转成 `[0,1]` 是代码映射，需重新校准入池阈值 |
| `style_key`、时效 class/mode/scope/state | 有限 Choice | 受控词表、unknown 选项、联合字段一致性验证 |
| `topic_group`、`franchise_key` | 从已有词表/当前候选提取结果中 Choice | 没有选项就不能生成新标签；需要 unknown、其他值策略或生成后端 |
| `reason`、时效 reason | 选择受控理由 code 或已有原句，再由模板组合 | 模板文字是代码生成；不等同于开放式理由生成 |
| `temporal_evidence` | 从预先抽取并编号的正文 span 中 Choice | 代码按 ID 取回原文并验证 grounding；不能让模型任意抄写新字符串 |
| `valid_until` 等精确时间 | 从本地已解析的有限日期候选中 Choice | 日期、时区、算数与证据一致性由代码负责；无可解析证据则 unknown |
| 一组 tags/多个兴趣 | 每个词表项一个 Noul，或若干受约束 Choice | 去重、数量、排序与互斥在代码；不是原生自由多选/自由 tag 生成 |
| 摘要、新实体名称、图片描述 | 无对应原生原语 | 需要已有内容抽取、受控模板或其他生成/视觉模型 |

官方推荐的抽取模式正是先用 regex/代码找到可能值，再让模型从编号候选中选择，最后由代码还原原始值。[Pre-parsed value extraction](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook)

只要问题相互独立、答案空间已准备好且输入符合双重上下文预算，一个请求可以同时给出整批候选所需的这些结构化判断；应用据此构造完整 metadata JSON。若 topic 的备选需前一步生成，或证据问题必须依据刚选出的 mode 才能构造，则需预先枚举分支并由代码选取，或分阶段调用。时效原子证据组尤其需要本地验证，不能因为同次返回就假设字段语义一致。[Speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)、[结构一致性限制](https://docs.typesafe.ai/model-jaggedness/jev-1.13)

## 实施前仍需验证的事项

- 官方说明 Jev 的主要训练语言是英语、CJK 表现较弱；完整中文候选评估必须单独验证，不能沿用英文演示的结论。[Models](https://docs.typesafe.ai/models)
- 官方承认精确算数、日期比较、间接推理、无关长上下文与对抗文本是已知弱点。字段类型合法不代表语义正确；扩大到完整 metadata 会增加联合错误风险。[Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
- 2026-10-02 官方限流为 `100K tokens/s`、`40 requests/s`，并声明限额会动态调整；成本仍是输入 `$0.042/百万 tokens`、输出免费。该数据已不同于 2026-09-26 笔记，部署时应再次核实。[Models](https://docs.typesafe.ai/models)
- 尚未实测官方实际超限响应、token 估计误差、完整字段的一致性、中文评估质量及批量延迟；本文不为这些结果作保证。

与此前研究的关系：本笔记补充 [2026-09-26 provider 研究](2026-09-26-jev-provider-research.md)，以本次核实值更新变化中的限额事实；不把研究判断标记为已实现功能。
