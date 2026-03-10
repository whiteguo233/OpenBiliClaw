# 记忆系统

> 五层网状记忆管理，从行为事件到深层画像，为所有 LLM 调用提供用户上下文。

## 概述

`memory/` 包实现了多层记忆架构，每一层从不同粒度理解用户：

| 层 | 名称 | 数据来源 | 存储 |
|----|------|----------|------|
| 事件层 | Event | 用户行为（点击/搜索/观看） | SQLite |
| 偏好层 | Preference | LLM 从事件提取的兴趣标签 | JSON |
| 觉察层 | Awareness | 每日觉察笔记 *(P2)* | JSON |
| 洞察层 | Insight | 假设管理 *(P2)* | JSON |
| 灵魂层 | Soul | 人格描述 + 核心特质 | JSON |

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 4.1 事件层 | ✅ | SQLite 写入 + 按类型/时间/关键词查询 + 统计 |
| 4.2 偏好层 | ✅ | LLM structured extraction + 合并 + 衰减 |
| 4.3 灵魂层 | ✅ | 初始画像生成 + `profile` CLI 展示 |
| 4.4 觉察层 + 洞察层 | ✅ | 觉察笔记、洞察假设、反馈更新 |
| 4.5 核心记忆加载 | ✅ | 统一摘要裁剪 + 所有 Soul LLM 调用自动注入 |
| 9.2 画像更新 | ✅ | 反馈达到阈值后自动重分析偏好，并持久化反馈处理状态 |
| 对话学习状态 | ✅ | `dialogue` 事件 + `insight_candidates.json`，支撑聊天信号的受控学习 |
| 持续刷新状态 | ✅ | `discovery_runtime.json` 记录候选池刷新、通知游标和最近处理事件位置 |
| 认知变化状态 | ✅ | `cognition_updates.json` 记录关键认知变化、通知状态和来源 |

## 公开 API

### MemoryManager

```python
from openbiliclaw.memory.manager import MemoryManager

memory = MemoryManager(data_dir=Path("data"))
memory.initialize()  # 创建目录 + 初始化 SQLite + 加载各层

# 写入事件
await memory.propagate_event({
    "event_type": "view",           # view|pause|seek|search|favorite|like|coin|comment|click|scroll|hover|snapshot|feedback
    "url": "https://www.bilibili.com/video/BV1xx",
    "title": "视频标题",
    "metadata": {"bvid": "BV1xx"},
})

# 查询事件
events = memory.query_events(
    event_types=["view", "search"],
    start_time=datetime(2026, 3, 1),
    keyword="纪录片",
    limit=50,
)

# 事件统计
stats = memory.get_event_stats()  # {"view": 42, "search": 7, ...}

# 层操作
layer = memory.get_layer("preference")
core_memory = memory.get_core_memory()
# {
#   "soul_summary": {...},
#   "preference_summary": {...},
#   "recent_awareness": [...],
#   "active_insights": [...],
# }

prompt_text = memory.render_core_memory_prompt()
# 返回固定区块："## 用户画像" / "## 偏好摘要" / "## 近期观察" / "## 当前洞察"

memory.save_all()

feedback_state = memory.load_feedback_state()
# {
#   "last_processed_feedback_event_id": 0,
#   "last_feedback_reanalyzed_at": ""
# }

runtime_state = memory.load_discovery_runtime_state()
# {
#   "last_event_refresh_at": "",
#   "last_trending_refresh_at": "",
#   "last_explore_refresh_at": "",
#   "last_processed_event_id": 0,
#   "last_notification_at": ""
# }

candidates = memory.load_insight_candidates()
# [
#   {
#     "id": "...",
#     "kind": "goal",
#     "content": "想更系统地理解国际局势",
#     "confidence": 0.84,
#     "occurrences": 2,
#     "applied": False,
#     ...
#   }
# ]

updates = memory.load_cognition_updates()
# [
#   {
#     "id": "cognition-...",
#     "kind": "interest_added",
#     "summary": "阿B 现在更确定你会吃“国际时事”这一口。",
#     "confidence": 0.86,
#     "source": "feedback",
#     "notified": False,
#     ...
#   }
# ]
```

### PreferenceAnalyzer（由 SoulEngine 调用）

```python
from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

analyzer = PreferenceAnalyzer(registry=llm_registry, decay_factor_per_week=0.9)
updated_pref = await analyzer.analyze_events(
    events=[...],
    existing_preference=current_pref,
)
# 返回格式化的偏好 dict，含 interests (带 weight/decay), style, context 等
```

## 配置项

```toml
[storage]
db_path = "data/openbiliclaw.db"

[general]
data_dir = "data"  # 记忆 JSON 文件存储在 data/memory/ 下
```

## 设计决策

1. **SQLite 事件层 + JSON 上层**：事件量大用 DB，画像数据量小用 JSON 文件
2. **兴趣衰减**：`weight × 0.9^weeks`，低于 0.05 自动移除，避免陈旧标签污染画像
3. **合并策略**：按 `(name, category)` 双键去重，权重取 max，`first_seen` 保持不变
4. **核心记忆裁剪**：`get_core_memory()` 只暴露稳定摘要，不把整层原始 JSON 直接塞进 prompt
5. **统一 Prompt 注入**：`render_core_memory_prompt()` 和 `LLMService` 统一为画像、偏好、觉察、洞察链路注入用户上下文
6. **插件事件兼容**：事件层白名单已扩到插件采集事件，避免 `/api/events` 在 `snapshot`、`scroll`、`hover`、`seek` 等行为上拒收
7. **反馈状态独立持久化**：`feedback_state.json` 单独保存反馈处理游标，避免把运行状态塞进 `preference.json` 或 `soul.json`
8. **聊天候选与正式画像分层**：聊天提取出的 `insight_candidates.json` 先作为中间状态保留，不直接覆盖 `soul.json`
9. **候选池运行状态分层**：`discovery_runtime.json` 只负责刷新与通知游标，不与 `feedback_state.json`、`insight_candidates.json` 或画像数据混存
10. **认知变化单独留痕**：`cognition_updates.json` 保存系统最近形成的关键理解变化，既供插件通知使用，也让画像页能回显“最近记住了什么”
