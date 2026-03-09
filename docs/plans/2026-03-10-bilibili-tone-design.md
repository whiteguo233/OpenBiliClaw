# B站语气动态化设计

## 背景
当前推荐文案、画像描述和聊天回复虽然已经可用，但整体仍偏“AI 解释型”表达，机械感较强。用户希望整体语气更像 B 站老朋友，同时又能根据系统对用户的长期理解逐步演化为更适合用户的表达方式。

## 目标
- 建立统一的 `tone profile` 机制
- 推荐、画像、聊天三条链共用同一套语气调节逻辑
- 基础风格为“老B友”，但能随用户画像动态调整
- 减少机械模板和心理报告感

## 方案选择
采用共享 `tone profile` helper 方案，不把语气只写死在单个 prompt 中。

原因：
- 避免推荐、画像、聊天各自朝不同方向演化
- 给“语气会随着理解而变化”一个稳定的实现入口
- 保持语气是派生信息，而不是直接固化进 `SoulProfile` 主数据结构

## tone profile 设计
新增一个轻量语气层，输出四个维度：
- `density`: `light | balanced | dense`
- `warmth`: `cool | warm | companion`
- `playfulness`: `low | medium | high`
- `directness`: `soft | balanced | direct`

推断依据来自：
- `SoulProfile`
- 偏好层摘要
- 最近反馈 / 最近觉察的少量上下文

## 三条链路的调整
### 推荐文案
从“AI 解释推荐”改成“老B友塞片”：
- 少用“这条内容正好对上你最近……”这类模板
- 多用更自然的 B 站朋友口吻，但避免油腻和过度热情

### 画像描述
从“心理报告”改成“熟人总结”：
- 允许 B 站语境词汇
- 不做病理化、报告体、咨询体表达
- 保留洞察感，但更像长期观察后的自然总结

### 聊天回复
保留追问和理解能力，但降低“机械苏格拉底感”：
- 更像顺着用户话头聊
- 允许轻度“老B友”语气
- 避免客服式和过度陪伴式表达

## 架构设计
### 新增模块
`src/openbiliclaw/soul/tone.py`
- 提供 `build_tone_profile(...)`
- 输入画像、偏好和近期上下文
- 输出结构化 tone profile

### Prompt 接入
在 `src/openbiliclaw/llm/prompts.py` 中：
- 推荐 prompt
- 画像 prompt
- 聊天 prompt
统一接入 tone profile，改写系统提示语气约束

## 错误处理与边界
- 若缺少完整画像或上下文，使用默认 `老B友` 中性风格：`balanced / warm / medium / balanced`
- 这轮不做 tone 的单独持久化文件
- 这轮不改 UI，只改后端生成语气

## 测试策略
### tone profile 测试
- 高信息密度画像 -> `density = dense`
- 探索开放度高 -> `playfulness` 提升
- 最近负反馈增多 -> `warmth` 提高、`directness` 降低

### prompt 测试
- 推荐 prompt 不再鼓励“解释型 AI 话术”
- 画像 prompt 减少报告体约束，改为“老朋友总结”
- 聊天 prompt 保留追问但更口语化

## 文档更新
实现完成后同步更新：
- `docs/modules/soul.md`
- `docs/modules/recommendation.md`
- `docs/modules/llm.md`
- `docs/modules/extension.md`
- `docs/changelog.md`
