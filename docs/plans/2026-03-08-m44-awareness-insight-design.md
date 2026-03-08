# 4.4 觉察层与洞察层设计

## 目标

实现真正可用的觉察层与洞察层：
- 基于近期事件、偏好层和灵魂画像生成每日觉察笔记
- 基于觉察笔记、偏好层和灵魂画像生成洞察假设
- 支持增量更新、去重、置信度和显式验证状态
- 将结果稳定持久化到 `awareness.json` 和 `insight.json`

## 设计边界

本阶段实现“可持续运行的轻量正式版”，不做完整知识图谱或自动验证闭环。

具体边界：
- `AwarenessNote` 和 `InsightHypothesis` 使用已有 dataclass
- 每次只生成少量高信号内容，不做流水账堆积
- `validated` 只接受显式反馈或代码规则更新，不让 LLM 自行宣布“已验证”
- 不联动重写 `soul.json`，觉察和洞察先作为并列层独立维护

## 架构方案

推荐新增两个分析器：
- `AwarenessAnalyzer`：将近期事件转换为结构化觉察笔记
- `InsightAnalyzer`：将觉察、偏好和灵魂画像转换为结构化洞察假设

`SoulEngine` 只负责编排：
- 查询最近事件
- 调用 analyzer
- 合并去重
- 保存到 memory layer
- 在反馈到来时更新洞察验证状态

## 数据流

### 觉察层

输入：
- 最近 24 小时或最近 N 条事件
- 当前偏好层
- 当前灵魂画像

输出：
- `list[AwarenessNote]`

规则：
- 同一天建议保留 1 到 3 条高信号观察
- 相近 observation 不重复追加
- trend 和 emotion_guess 必须使用保守表述

### 洞察层

输入：
- 最近觉察笔记
- 当前偏好层
- 当前灵魂画像
- 必要时补少量原始事件标题作为证据

输出：
- `list[InsightHypothesis]`

规则：
- 每条假设必须附 1 到 3 条 evidence
- confidence 保持保守
- 相近假设合并，不无限新增
- validated 默认 `False`

## Prompt 设计

### 觉察 prompt

目标是“近期观察”，不是人格定论。

输入分段：
- 任务说明：总结最近行为变化，避免诊断式语言
- 近期事件摘要：标题、类型、时间、关键词
- 偏好摘要与画像摘要：作为理解背景

输出为严格 JSON 数组：
- `date`
- `observation`
- `trend`
- `emotion_guess`

### 洞察 prompt

目标是“解释性假设”，不是结论。

输入分段：
- 任务说明：提出谨慎假设
- 最近觉察笔记
- 偏好摘要
- 灵魂画像摘要

输出为严格 JSON 数组：
- `hypothesis`
- `evidence`
- `confidence`

`validated` 和 `created_at` 由本地代码补充，不信任模型生成。

## 状态管理与去重

### 觉察去重

使用简单规则去重：
- 同日
- observation 文本标准化后完全相同，或前缀高度相似

重复时：
- 保留已有条目
- 新条目只在信息明显增量时追加

### 洞察合并

使用保守规则合并：
- hypothesis 标准化后完全相同，或文本包含关系明显

合并时：
- evidence 去重并合并
- confidence 取较高值但限制上限
- validated 以已有显式状态为准

## 反馈更新

`SoulEngine.update_from_feedback(feedback)` 这轮至少完成：
- 将反馈作为 `feedback` 事件写入事件层
- 如果 feedback 指向某条假设：
  - `confirm`/`like` 类反馈可提高 confidence 或标记 `validated=True`
  - `reject`/`dislike` 类反馈可降低 confidence 或保持 `validated=False`

## 错误处理

新增：
- `AwarenessGenerationError`
- `InsightGenerationError`

当出现空响应、坏 JSON、字段缺失时：
- 记录日志
- 不覆盖现有 `awareness.json` / `insight.json`
- 调用方获得明确异常

若近期事件不足：
- 觉察层允许返回空列表
- 洞察层允许跳过，不强行生成

## 测试策略

单元测试覆盖：
- `AwarenessAnalyzer` 成功生成 `AwarenessNote`
- `AwarenessAnalyzer` 对坏 JSON/空响应报错
- 同日重复觉察会去重
- `InsightAnalyzer` 成功生成 `InsightHypothesis`
- 相近假设会合并，`validated` 默认保持 `False`
- `SoulEngine.generate_awareness_note()` 会保存 awareness 层
- `SoulEngine.generate_insight()` 会保存 insight 层
- `SoulEngine.update_from_feedback()` 会写 feedback 事件并更新洞察状态

## 影响文件

- 新增 `src/openbiliclaw/soul/awareness_analyzer.py`
- 新增 `src/openbiliclaw/soul/insight_analyzer.py`
- 修改 `src/openbiliclaw/llm/prompts.py`
- 修改 `src/openbiliclaw/soul/engine.py`
- 可能扩展 `src/openbiliclaw/soul/profile.py`
- 新增测试：
  - `tests/test_awareness_analyzer.py`
  - `tests/test_insight_analyzer.py`
  - 扩展 `tests/test_soul_engine.py`
