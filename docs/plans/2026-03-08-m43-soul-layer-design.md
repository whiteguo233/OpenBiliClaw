# 4.3 灵魂层设计

## 目标

实现 `SoulProfile` 的首次生成、持久化与展示闭环：
- 从用户历史摘要和现有偏好层生成初始画像
- 将画像保存到 `data/memory/soul.json`
- 为后续 LLM 调用提供稳定的画像上下文
- 将 `openbiliclaw profile` 从占位输出升级为真实展示

## 设计边界

本阶段只实现灵魂层本身，不把真实 B 站历史抓取和 `openbiliclaw init` 一起接入。`init` 在后续 `7.1` 中只负责编排：拉取历史、调用 `SoulEngine.build_initial_profile()`、展示结果。

这次不实现觉察层与洞察层生成逻辑，`recent_awareness` 和 `active_insights` 允许为空。

## 架构方案

推荐方案是新增独立的 `profile_builder.py`：
- `SoulEngine` 继续负责编排
- `ProfileBuilder` 负责 prompt 组装、LLM 调用结果解析、`SoulProfile` 构建
- `MemoryManager` 继续负责 `soul` 层 JSON 的读取与保存

这样可以避免把“画像推理逻辑”塞进 `SoulEngine`，也避免后续 `init`、`profile`、推荐表达等模块重复处理 LLM 输出。

## 数据流

`SoulEngine.build_initial_profile(history)` 的执行流程：
1. 从 `MemoryManager` 读取已有 `preference` 层
2. 将 `history` 压缩成结构化摘要
3. 将 `history` 摘要和 `preference` 摘要注入画像生成 prompt
4. 调用 LLM 生成严格 JSON
5. 解析为 `SoulProfile`
6. 保存到 `data/memory/soul.json`
7. 返回 `SoulProfile`

`SoulEngine.get_profile()` 的执行流程：
1. 读取 `soul` 层数据
2. 若为空，返回明确错误或状态
3. 若存在，反序列化为 `SoulProfile`

## Prompt 设计

画像 prompt 保持结构化输出，而不是直接让模型自由写长文。输入分为三段：
- 任务说明：像长期观察后的朋友一样描述此人，避免医学化和过度诊断
- 历史摘要：主题偏好、常看内容、行为模式、时间分布
- 偏好摘要：兴趣标签、风格偏好、探索倾向、讨厌主题、喜欢的 UP 主

输出严格限制为：
- `personality_portrait`
- `core_traits`
- `values`
- `life_stage`
- `deep_needs`

约束：
- `personality_portrait` 至少 200 中文字
- `core_traits` 为 3 到 5 项
- 所有判断基于行为证据，语气保持谨慎

## 错误处理

新增 `SoulProfileBuildError`，用于处理：
- LLM 空响应
- 非 JSON 响应
- JSON 字段缺失或类型错误
- 画像长度明显不达标

失败时不覆盖已有 `soul.json`。如果 `history` 较少，则允许生成画像，但 prompt 必须要求模型降低确定性、使用保守措辞。

## CLI 展示

`openbiliclaw profile` 改为真实展示：
- 人格描述
- 核心特质
- 价值观
- 当前人生阶段
- 深层需求

若画像不存在，则提示用户后续执行 `openbiliclaw init` 完成初始化。

## 测试策略

单元测试覆盖：
- `ProfileBuilder` 成功将 JSON 转为 `SoulProfile`
- 空响应、坏 JSON、缺字段时报明确错误
- 无偏好数据时仍可生成画像
- `SoulEngine.build_initial_profile()` 会保存 `soul.json`
- `SoulEngine.get_profile()` 能读取已有画像，缺失时返回明确状态
- `profile` CLI 有画像和无画像两种输出

## 影响文件

- 新增 `src/openbiliclaw/soul/profile_builder.py`
- 修改 `src/openbiliclaw/soul/engine.py`
- 修改 `src/openbiliclaw/llm/prompts.py`
- 可能扩展 `src/openbiliclaw/soul/profile.py` 的序列化辅助
- 修改 `src/openbiliclaw/cli.py`
- 新增或扩展测试：
  - `tests/test_profile_builder.py`
  - `tests/test_soul_engine.py`
  - `tests/test_cli.py`
