# Explore Cluster Cap And Refresh Copy Design

**Date:** 2026-03-23

## Goal

同时解决两个体验问题：

1. `explore` 候选池容易被单一子主题簇灌满，例如制造 / 工艺 / 材料 / 显微这一类。
2. popup 里的“这轮还没补进 / 这轮没补进新的候选”会把“当前正在补货”和“上一轮净新增为 0”混成一个状态，语义不准。

## Root Cause

### 1. `explore` 只做来源级平衡，没有子主题簇约束

当前 runtime refresh 会按 `search / related_chain / trending / explore` 做来源级补货目标，但不会限制 `explore` 内部某个相近子主题簇的累计占比。

结果是即使 `explore` 总量没有超标，仍然可能在内部积累大量：

- 精密制造纪录片
- 工业设计纪录片
- 芯片显微镜逆向工程
- 金属疲劳事故分析

这类本质接近的条目。

### 2. 新 prompt 只影响未来，不会清理旧池子

前一轮已经写入 `content_cache` 的制造类 `explore` 候选不会因为 prompt 更新自动消失。当前“换一换”看到的大量制造内容，很多来自旧库存，而不是新 prompt 仍然失效。

### 3. `last_replenished_count` 表示的是净新增库存，不是本轮发现量

后端当前把：

`after_pool_count - before_pool_count`

写成 `last_replenished_count`。这表示的是“本轮结束后，可立即换库存净增了多少”，不是“这轮 discover 找到了多少条内容”。

因此一轮 refresh 完全可能：

- 实际 discover 到了新内容
- 也写进了 `content_cache`
- 但因为重复 `bvid`、已推荐过、或只是覆盖更新老行
- 最终 `last_replenished_count == 0`

这时后端和前端都会显示“没补进新的候选”，但用户体感上会误以为“补货没跑”。

### 4. 前端状态摘要没区分 running 和 zero-result

`getPoolStatusSummary()` 当前只看：

- `pool_available_count`
- `last_replenished_count`

不会优先判断 `manual_refresh_state == "running"`。所以 refresh 还在跑时，也可能先显示“这轮还没补进”。

## Chosen Approach

采用中间方案，同时修“库存结构”和“状态语义”：

1. 给 `explore` 增加子主题簇上限
2. 对现有池子中明显过量的制造类 `explore` 做温和清理
3. 给 runtime status 增加“本轮实际发现量”字段
4. 前端状态文案按 `running / discovered / net_added` 分开表达

## Design

### A. `explore` 子主题簇上限

在 runtime refresh 结束后、写状态前，对本轮 `explore` 产出的候选做一层轻量簇约束。

不做重型 topic clustering，只做最小可维护规则：

- 基于 `topic_key + title` 提取若干稳定关键词
- 归并出少量高风险簇，例如：
  - 制造 / 工艺 / 工厂 / 工业
  - 材料 / 金属 / 芯片 / 显微 / 纳米
  - 博弈 / 桌游 / 机制 / 策略模型
- 同一高风险簇在 fresh pool 中超过上限时，本轮新发现的同簇 `explore` 内容不再继续写入池子

这层目标不是“精确聚类”，而是防止明显单簇持续堆积。

### B. 旧制造库存温和清理

不直接删库，不动其他来源。

仅对 `content_cache` 里 `source='explore'` 且命中高风险制造簇、并且超过簇上限的条目，做温和降级：

- 优先把过量部分标成不可立即参与 `reshuffle` 的状态
- 保留已有推荐历史和原始缓存行

这样能让新 prompt 和新补货更快占回可换窗口。

### C. refresh 状态拆成两个计数

在 runtime state 里新增：

- `last_discovered_count`
  本轮 discover 实际拿到了多少条候选
- `last_replenished_count`
  本轮可立即换库存净新增多少条

这样用户能分清：

- “这轮跑了，但净新增 0”
- “这轮根本还没跑完”
- “这轮确实补进了一批可换的新内容”

### D. popup 文案改成状态机

pool summary 和 ready hint 改成优先按状态表达：

- `manual_refresh_state == running`
  显示“正在补货”
- `manual_refresh_state == success && last_replenished_count > 0`
  显示“刚补进 N 条”
- `manual_refresh_state == success && last_replenished_count == 0 && last_discovered_count > 0`
  显示“这轮找到了内容，但可立即换库存没变”
- `manual_refresh_state == success && last_discovered_count == 0`
  显示“这轮没找到新的方向”

## Tradeoffs

### Option 1: 只改 prompt 和前端文案

- 优点：最小改动
- 缺点：旧池子和 `explore` 单簇堆积问题不解决

### Option 2: 推荐方案

- 优点：能同时处理旧库存、未来补货和状态语义
- 缺点：要新增少量池子治理逻辑和测试

### Option 3: 上重型 topic clustering

- 优点：长期最稳
- 缺点：范围过大，不适合这轮修复

## Testing Strategy

新增或更新测试覆盖：

1. runtime refresh 在发现内容但净新增为 0 时，正确写入 `last_discovered_count > 0` 和 `last_replenished_count == 0`
2. popup 状态摘要在 `running` 时不再显示“这轮还没补进”
3. `explore` 高风险子簇超过上限时，不再继续扩大该簇的 fresh pool 占比
4. 旧制造类 `explore` 过量库存会被温和降级，释放可换窗口

## Docs Impact

更新：

- `docs/modules/discovery.md`
- `docs/modules/recommendation.md`
- `docs/modules/extension.md`
- `docs/changelog.md`
