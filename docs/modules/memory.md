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

## 现在画像相关到底有几层

如果从“系统怎么一步步理解你”这个角度看，当前不是一层画像，而是 **5 层**：

1. **事件层 Event**
   这里只记发生了什么，不做解释。
   例如：
   - 你看了哪条视频
   - 搜了什么词
   - 点了 like / dislike
   - 聊天里说了什么

2. **偏好层 Preference**
   这里开始回答“你大概率喜欢什么、不喜欢什么、习惯怎么看”。
   例如：
   - `interests = 国际时事 / 历史 / 纪录片`
   - `depth_preference = 0.9`
   - `disliked_topics = 浅层热点复读`

3. **觉察层 Awareness**
   这里不是给你贴人格标签，而是写“最近观察到的变化”。
   例如：
   - “最近连续浏览高信息密度国际议题内容”
   - “这几天明显更偏向讲透结构，而不是轻量消遣”

4. **洞察层 Insight**
   这里开始尝试解释“你为什么会这样选内容”，但仍然保持假设语气。
   例如：
   - “用户可能不是只想知道发生了什么，而是在用深度内容建立判断确定性”

5. **灵魂层 Soul**
   这是最终画像层，回答的是“这个人整体上像谁、怎么理解世界、最近处于什么阶段”。
   例如：
   - “这是一个会主动追问复杂事件底层逻辑的人，偏好高信息密度、能把因果链讲透的内容”

### 一句话区分这五层

- **事件层**：发生了什么
- **偏好层**：你喜欢什么
- **觉察层**：你最近变成了什么样
- **洞察层**：你可能为什么会这样
- **灵魂层**：把前面这些长期整合后，你像一个什么样的人

### 一个直观例子

假设你最近连续做了这些事：

- 看了 3 条“国际局势深度解读”
- 搜索“国际新闻 因果链”
- 聊天里说“我想把国际新闻背后的结构看明白”
- 对一条“浅层热点复读”点了 `dislike`

那么五层大致会这样长出来：

| 层 | 这一层会记录什么 | 示例 |
|----|------------------|------|
| Event | 原始事实 | `view: 20分钟讲透中东局势`、`search: 国际新闻 因果链`、`feedback: dislike` |
| Preference | 稳定偏好信号 | 你偏好高信息密度、深度解释，不喜欢浅层复读 |
| Awareness | 近期观察 | 最近在连续寻找“能把事情讲透”的内容 |
| Insight | 解释性假设 | 你可能在通过深度内容建立更稳定的判断框架 |
| Soul | 长期画像 | 你是一个倾向追问结构和因果、对表层热闹不太满足的人 |

所以当前“画像”不是一张平面的标签表，而是一套从事实到解释再到人格描述的分层结构。

### 为什么要分这么多层

如果只有最终画像一层，系统会有两个问题：

- **太容易抖动**：你今天随口说一句话，整张人格画像就变
- **太难解释**：前端只能看到“你现在是这样的人”，但说不清这个判断从哪里来

分层之后，每一层各做一件事：

- Event 保证原始证据可追溯
- Preference 保证推荐和 discovery 有稳定结构化输入
- Awareness / Insight 保证画像不只是兴趣标签堆砌
- Soul 保证最终输出仍然像“对一个人的理解”，而不是报表

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 4.1 事件层 | ✅ | SQLite 写入 + 按类型/时间/关键词查询 + 统计；v0.3.x 每行带 `inferred_satisfaction` + `satisfaction_reason`（写入时由 `classify_event_satisfaction` 决定），`query_events(satisfaction_modes=...)` 支持按 positive/negative/neutral/unknown 过滤，`unknown` 同时匹配 pre-migration 的 NULL 行 |
| 4.2 偏好层 | ✅ | LLM structured extraction + 合并 + 衰减 |
| 4.3 灵魂层 | ✅ | 初始画像生成 + `profile` CLI 展示 |
| 4.4 觉察层 + 洞察层 | ✅ | 觉察笔记、洞察假设、反馈更新 |
| 4.5 核心记忆加载 | ✅ | 统一摘要裁剪 + 所有 Soul LLM 调用自动注入 |
| 9.2 画像更新 | ✅ | 反馈达到阈值后自动重分析偏好，并持久化反馈处理状态 |
| 对话学习状态 | ✅ | `dialogue` 事件 + `insight_candidates.json`，支撑聊天信号的受控学习 |
| 持续刷新状态 | ✅ | `discovery_runtime.json` 记录候选池刷新、通知游标、最近处理事件位置、正向/负向 probe 冷却、probe distance 历史和短期探索 buffer |
| 认知变化状态 | ✅ | `cognition_updates.json` 记录关键认知变化、通知状态和来源 |
| 账户同步状态 | ✅ | `account_sync_state.json` 记录历史/收藏/关注同步游标、已见 ID 集合、签名和最近错误 |
| 多源 bootstrap 去重状态 | ✅ | `source_bootstrap_state.json` 记录 XHS / 抖音 / YouTube 已进入事件路径的 bootstrap identity key，避免跨任务重放旧画像信号 |
| 用户画像覆盖层 | ✅ | `profile_overrides.json` 存用户对画像的手动编辑（文本/标量固定 + 列表/兴趣树增删）；`load/save_profile_overrides` 读写，`sync_profile_files` 渲染人类可读镜像前叠加覆盖层，确保编辑在画像重建后仍反映在 `soul_profile.md/.json` |
| 插件聊天回合 | ✅ | SQLite `chat_turns` 持久化 side panel 主聊天、惊喜推荐内聊、兴趣猜测内聊和避雷探针内聊的 pending/completed/failed 状态 |
| JSON 状态原子更新 | ✅ | `memory/json_state.py` 提供带进程内锁、跨进程文件锁和 `os.replace` 的 `update_json_state()`；`discovery_runtime.json` 的 probe 反馈历史、冷却 map、短期探索 buffer 等运行态通过 mutator 更新并合并旧快照，避免安装包常驻进程/后台任务并发保存时丢掉用户点击反馈 |

## 公开 API

### MemoryManager

```python
from openbiliclaw.memory.manager import MemoryManager

memory = MemoryManager(data_dir=Path("data"))
memory.initialize()  # 创建目录 + 初始化 SQLite + 加载各层

# 运行时可注入共享 Database，避免同进程里重复建立 SQLite 连接
memory = MemoryManager(data_dir=Path("data"), database=shared_database)
memory.initialize()

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

# 插件 side panel 的 durable chat turn 由 Database 管理：
from openbiliclaw.storage.database import Database

database = Database(Path("data/openbiliclaw.db"))
database.initialize()
turn = database.create_chat_turn(
    turn_id="turn-...",
    session="popup",
    scope="chat",  # chat|delight|probe|avoidance_probe
    message="我想继续聊聊这个方向",
)
database.complete_chat_turn(turn["turn_id"], reply="这句我记下了。")
history = database.list_chat_turns(session="popup", scope="chat")

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
#   "last_notification_at": "",
#   "probed_distance_bands": {},
#   "short_term_exploration_buffer": {"entries": []}
# }

def remember_probe_feedback(state):
    state.setdefault("probe_feedback_history", []).append(
        {"domain": "建筑美学", "response": "confirm", "created_at": "2026-06-09T12:00:00+00:00"}
    )

runtime_state = memory.update_discovery_runtime_state(remember_probe_feedback)
# 原子读改写 discovery_runtime.json，并和磁盘上较新的 probe 反馈/冷却状态合并。

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

account_sync_state = memory.load_account_sync_state()
# {
#   "last_history_view_at": 1710000000,
#   "last_history_bvid": "BV1SYNC",
#   "history_bvids_at_last_view_at": ["BV1SYNC", "BV2SYNC"],
#   "last_favorites_sync_at": "2026-03-14T12:00:00+00:00",
#   "favorite_signature": "7:BVFRESH",
#   "favorite_bvids": ["BVFRESH"],
#   "last_following_sync_at": "2026-03-14T12:05:00+00:00",
#   "following_signature": "99",
#   "following_mids": ["99"],
#   "last_account_sync_at": "2026-03-14T12:05:00+00:00",
#   "last_sync_error": "",
# }

source_bootstrap_state = memory.load_source_bootstrap_state()
# {
#   "xhs_seen_note_keys": ["saved:note-id"],
#   "dy_seen_video_keys": ["dy_collect:aweme-id"],
#   "yt_seen_item_keys": ["yt_history:video-id"],
#   "last_source_bootstrap_sync_at": "2026-05-20T12:00:00+00:00",
# }
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

## 示例：记忆如何组织与更新

下面用一个具体场景说明当前实现里的记忆结构和更新机制。

### 场景

假设用户最近连续出现这些信号：

- 看了几条“国际时事深度解读”视频
- 搜索过“国际新闻 因果链”
- 在聊天里说“我更想把国际新闻背后的结构看明白”
- 对一条浅层热点复读推荐点了 `dislike`

### 这组信号会分别落到哪一层

1. **事件层 Event**
   所有 `view` / `search` / `dialogue` / `feedback` 先进入 SQLite 事件表，作为原始事实。
2. **偏好层 Preference**
   `SoulEngine.analyze_events()` 或 `SoulEngine.process_feedback_batch_if_needed()` 会调用 `PreferenceAnalyzer`，把事件提取成结构化偏好，例如：
   - `interests`: `国际时事`
   - `style.depth_preference`: 更高
   - `disliked_topics`: 新增“浅层热点复读”
3. **觉察层 Awareness**
   `SoulEngine.generate_awareness_note()` 会把近期事件总结成观察，例如：
   - “最近连续浏览高信息密度国际议题内容”
4. **洞察层 Insight**
   `SoulEngine.generate_insight()` 会基于觉察、偏好和画像形成假设，例如：
   - “他不是只想知道发生了什么，而是想看清事件背后的因果结构”
5. **灵魂层 Soul**
   当偏好变化足够明显时，`SoulEngine` 会重建 `soul.json`，把这些变化沉淀成更稳定的人格化描述，例如：
   - “这是一个会主动追问复杂事件底层逻辑的人”

### 更新机制

当前实现里，`MemoryManager.propagate_event()` 的职责是**接收并持久化事件**。它不会在写入事件后自动把五层全部向上刷新。

真正的更新链路由上层编排触发：

1. **行为事件写入**
   CLI、API、插件或账户同步先调用 `propagate_event()` 落库。
2. **偏好层更新**
   `SoulEngine.analyze_events()` 会把一批事件送进 `PreferenceAnalyzer`。
   合并时会：
   - 按 `(name, category)` 去重
   - 保留 `first_seen`
   - 更新 `last_seen`
   - 权重取较大值
3. **兴趣衰减**
   已有兴趣会按 `weight × 0.9^weeks` 衰减，低于 `0.05` 自动移除，避免旧兴趣长期污染推荐。
4. **反馈批量学习**
   推荐反馈不会每条都立刻重建画像；默认累计到 `3` 条新的 `feedback` 事件，`process_feedback_batch_if_needed()` 才触发一次偏好重分析。
5. **聊天信号受控学习**
   聊天提取出的长期信号会先写到 `insight_candidates.json`。
   只有当候选满足：
   - `confidence >= 0.8`
   - `occurrences >= 2`
   才会正式转换成 `dialogue_insight` 事件去更新偏好层。
6. **画像重建阈值**
   只有高权重兴趣明显变化，或者新增了明确的 `disliked_topics`，才会重建 `SoulProfile`，避免单次噪声把人格画像来回抖动。

### 这个场景下可能出现的中间状态

```json
{
  "preference": {
    "interests": [
      {
        "name": "国际时事",
        "category": "知识",
        "weight": 0.88,
        "source": "dialogue"
      }
    ],
    "disliked_topics": ["浅层热点复读"]
  },
  "awareness": {
    "notes": [
      {
        "observation": "最近连续浏览高信息密度国际议题内容。"
      }
    ]
  },
  "insight": {
    "hypotheses": [
      {
        "hypothesis": "用户正在寻找能解释国际事件因果链的内容。",
        "confidence": 0.84
      }
    ]
  }
}
```

### 核心记忆如何被上层消费

不是所有原始 JSON 都会直接喂给 LLM。`get_core_memory()` 只裁剪出稳定摘要：

- `soul_summary`
- `preference_summary`
- `recent_awareness`
- `active_insights`

`LLMService.complete_structured_task()` 会把这份 core memory 自动注入到后续的偏好分析、觉察、洞察、聊天学习和 discovery 评分 prompt 里，让系统在“记得你是谁”的前提下继续理解新信号。

## 状态文件体系

除五层记忆数据外，MemoryManager 还管理一组运行状态文件：

```
data/memory/
├── event.json                  # 事件层（层数据缓存）
├── preference.json             # 偏好层
├── awareness.json              # 觉察层
├── insight.json                # 洞察层
├── soul.json                   # 灵魂层
├── feedback_state.json         # 反馈处理游标
├── account_sync_state.json     # 账户同步游标
├── source_bootstrap_state.json # 多源 bootstrap 已见 identity key
├── discovery_runtime.json      # 候选池刷新游标
├── avoidance_state.json        # 不喜欢领域探针 active/cooldown 状态
├── insight_candidates.json     # 聊天候选洞察（中间态）
└── cognition_updates.json      # 认知变化记录（供插件通知）
```

| 文件 | 用途 | 主要消费者 |
|------|------|-----------|
| `feedback_state.json` | 记录反馈处理到了哪一条，避免重复分析 | SoulEngine |
| `account_sync_state.json` | 历史/收藏/关注的增量同步游标、同秒历史 bvid 集合、收藏 bvid 集合、关注 mid 集合和签名 | AccountSyncService |
| `source_bootstrap_state.json` | XHS / 抖音 / YouTube bootstrap 已传播 identity key，避免跨任务重复写入同一批画像信号 | FastAPI source task endpoints |
| `discovery_runtime.json` | 候选池刷新时间、通知游标、最近话题、近期 probe domain / axis / distance 历史、显式 probe feedback 历史、短期探索 buffer | RefreshController / OpenClaw / FastAPI |
| `avoidance_state.json` | 不喜欢领域探针的 active/cooldown 列表和生命周期状态 | AvoidanceSpeculator / FastAPI |
| `insight_candidates.json` | 聊天中提取的候选洞察，等待置信度达标 | SoulEngine |
| `cognition_updates.json` | 系统最近形成的关键认知变化 | FastAPI → 浏览器插件通知 |

设计原则：每种状态独立文件，不和画像数据混存。

`discovery_runtime.json` 里与兴趣探针相关的字段：

| 字段 | 结构 | 说明 |
|------|------|------|
| `probed_domains` | `{normalized_domain: iso_timestamp}` | 近期已成功推送到 runtime stream / 已由 OpenClaw 返回的 probe domain，用于短期避免重复问同一方向；前端离线导致未投递时不写入 |
| `probed_axes` | `{experience_mode|entry_load: iso_timestamp}` | 近期已成功推送到 runtime stream / 已由 OpenClaw 返回的体验轴，用于在验证压力相同的候选中优先选择不同体验；前端离线导致未投递时不写入 |
| `probed_distance_bands` | `{probe_mode: iso_timestamp}` | 近期已成功推送到 runtime stream / 已由 OpenClaw 返回的 `near/lateral/bridge/wildcard` 距离档，用于在同等压力下优先选择没问过的挑战距离 |
| `probe_feedback_history` | `[{domain,response,axis?,category?,reason?,specifics?,message?,raw_text_excerpt?,classification?,classifier?,resulting_action?,created_at}]` | 最近 100 条用户显式探针反馈；reject / chat_rejected 会参与后续 novelty guard 与 probe selection，weak_positive 只进入探索 buffer，confirm / chat_confirmed / neutral 作为审计记录 |
| `short_term_exploration_buffer` | `{"entries": [{domain,specifics,buffer_key,score,first_seen,last_seen,expires_at,positive_event_count,explicit_event_count,cooldown_until,recent_evidence}]}` | 兴趣探针弱正向、推荐喜欢、惊喜喜欢、普通点击和负反馈的短期聚合状态；晋升后以 `buffer_promoted` 写入长期兴趣 |
| `probed_avoidance_domains` | `{normalized_domain: iso_timestamp}` | 近期已成功推送到 runtime stream / 已由 OpenClaw 返回的不喜欢领域探针 domain，用于避免重复问同一避雷方向 |
| `probed_avoidance_axes` | `{experience_mode|entry_load: iso_timestamp}` | 近期已成功推送到 runtime stream / 已由 OpenClaw 返回的不喜欢领域体验轴，用于在同等压力下优先选择不同形态 |
| `avoidance_probe_feedback_history` | `[{domain,response,axis?,source_mode?,reason?,specifics?,message?,created_at}]` | 最近 100 条避雷探针反馈；否认或 positive-like 聊天会抑制重复候选，confirm / avoidance_chat_confirmed 会进入 confirmed dislike 写回链路 |
| `last_probe_kind` | `"interest" | "avoidance" | ""` | 主动推送循环的正向/负向 probe 轮转状态；只有实际投递成功后才更新 |

## 系统集成

MemoryManager 被以下组件直接依赖：

| 组件 | 读操作 | 写操作 |
|------|--------|--------|
| **SoulEngine** | 全部五层 + feedback/insight/cognition 状态 | 全部五层 + feedback/insight/cognition 状态 |
| **LLMService** | `render_core_memory_prompt()`, `get_core_memory()` | — |
| **FastAPI (app.py)** | `load_cognition_updates()` | `propagate_event()`, `save_cognition_updates()` |
| **Source task endpoints** | `load_source_bootstrap_state()` | `save_source_bootstrap_state()`, `propagate_event()` |
| **RefreshController** | `load_discovery_runtime_state()` | `save_discovery_runtime_state()` |
| **AccountSyncService** | `load_account_sync_state()` | `save_account_sync_state()`, `propagate_event()` |
| **CLI** | — | `propagate_event()` |

职责边界：**SoulEngine** 是唯一负责五层之间编排和更新逻辑的组件，其他组件只通过 MemoryManager 的公开接口读写特定状态。

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
3. **运行时共享 SQLite 实例**：CLI / API 高流量路径优先复用同一个 `Database`，减少锁冲突和重复初始化
4. **合并策略**：按 `(name, category)` 双键去重，权重取 max，`first_seen` 保持不变
5. **灵魂层双存储**：`soul_profile.json` 存储结构化 OnionProfile v2 格式（版本 2），`soul_profile.md` 提供人类可读的镜像，`soul_changelog.md` 记录每次更新的来源、时间和变化摘要；自动迁移 v1 flat SoulProfile 格式
6. **核心记忆裁剪**：`get_core_memory()` 只暴露稳定摘要，不把整层原始 JSON 直接塞进 prompt
7. **统一 Prompt 注入**：`render_core_memory_prompt()` 和 `LLMService` 统一为画像、偏好、觉察、洞察链路注入用户上下文
8. **插件事件兼容**：事件层白名单已扩到插件采集事件，避免 `/api/events` 在 `snapshot`、`scroll`、`hover`、`seek` 等行为上拒收
9. **反馈状态独立持久化**：`feedback_state.json` 单独保存反馈处理游标，避免把运行状态塞进 `preference.json` 或 `soul.json`
10. **聊天候选与正式画像分层**：聊天提取出的 `insight_candidates.json` 先作为中间状态保留，不直接覆盖 `soul.json`
11. **插件聊天回合独立持久化**：`chat_turns` 只保存 side panel durable turn 的请求、回复和状态，解决 Chrome side panel reload / discard 时 DOM 和 JS 内存丢失的问题；它不替代事件层学习，完成后的 dialogue/cognition 仍按后端流程受控进入画像链路
12. **候选池运行状态分层**：`discovery_runtime.json` 只负责刷新与通知游标，不与 `feedback_state.json`、`insight_candidates.json` 或画像数据混存
13. **认知变化单独留痕**：`cognition_updates.json` 保存系统最近形成的关键理解变化，既供插件通知使用，也让画像页能回显”最近记住了什么”
14. **账户同步状态单独持久化**：`account_sync_state.json` 记录 history / favorites / following 的增量游标、已见 ID 集合和稳定签名，避免每轮全量重灌事件层，也避免收藏夹顺序变化或同秒历史游标导致重复画像分析
15. **多源 bootstrap 去重状态独立持久化**：`source_bootstrap_state.json` 只保存 XHS / 抖音 / YouTube 已见 bootstrap identity key，不塞进画像 JSON；task-result 仍保留完整原始结果用于调试，但进入 memory / profile pipeline 前会过滤旧 key
