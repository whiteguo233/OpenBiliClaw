# Explore Domain Prompt Diversity Design

**Date:** 2026-03-23

## Goal

在保留 `ExploreStrategy` 跨领域外推能力的前提下，减少 LLM 围绕单一认知轴连续输出近义 domain 的情况，让 `explore` 候选方向更分散。

## Problem

当前 `build_explore_domains_prompt()` 只约束了：

- 输出 JSON
- 不要直接重复现有高权重兴趣
- 解释为什么会打动用户
- 给出 1 到 2 个 query

它没有约束：

- 多个 domain 之间必须覆盖不同内容方向
- 不允许围绕同一个抽象母题换皮输出多个 domain
- `why_it_might_resonate` 应该先从认知需求出发，而不是仅按题材近邻联想

结果是当用户画像中出现 `机制 / 策略 / 底层逻辑 / 杀戮尖塔2` 一类信号时，模型容易把多个外推位都压到 `博弈论 / 桌游机制 / 纳什均衡 / 策略模型` 这一个簇里。

## Chosen Approach

只改 prompt，不改 `ExploreStrategy` 的结果过滤或后处理逻辑。

### Why this approach

- 这是这轮最小改动，风险最低。
- 可以先验证“约束不够”是否就是主要原因。
- 不会引入新的 heuristic 或 topic-clustering 代码。

### Tradeoffs

- 优点：实现快，回归风险小。
- 缺点：稳定性仍依赖模型遵守 prompt，不能像结果过滤那样硬性兜底。

## Prompt Changes

在 `build_explore_domains_prompt()` 中增加三类约束：

1. **方向覆盖约束**
   `domains` 必须分散到至少 3 类不同内容方向，例如：
   - 知识解释
   - 现实观察
   - 审美体验
   - 人物叙事
   - 技术机制
   - 社会文化

2. **单轴换皮禁止**
   如果多个候选 domain 本质上都在讲同一母题，只能保留 1 个。示例中明确禁止：
   - `博弈论 / 桌游机制 / 纳什均衡 / 策略模型`
   - 其他只是同一抽象轴换词的近义项

3. **认知需求优先**
   `why_it_might_resonate` 要先解释它对应用户的哪种信息处理方式、观看需求或内在驱动力，而不是只说“和他原本兴趣相似”。

## Testing Strategy

新增一个 prompt builder 单测，锁住 system prompt 中的关键规则，至少断言：

- 提到了“至少 3 类不同内容方向”
- 提到了“同一母题换皮只能保留 1 个”
- 提到了 `why_it_might_resonate` 需要从认知需求出发

## Docs Impact

更新：

- `docs/modules/discovery.md`
- `docs/changelog.md`

说明 `ExploreStrategy` 的 prompt 现在会主动约束跨域外推的方向多样性，避免单一相邻主题灌池。
