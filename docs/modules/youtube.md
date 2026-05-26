# YouTube 模块

## 概述

YouTube 模块负责把用户在 YouTube 上的长期兴趣信号接入 OpenBiliClaw 画像链路，并在 discovery 阶段把 YouTube 视频作为候选供给。当前有四类入口：

- 浏览器扩展任务桥：`openbiliclaw init --yes-youtube` / `fetch-youtube` 入队 `yt_tasks(type="bootstrap_profile")`，扩展在已登录 YouTube 会话中读取观看历史、订阅和点赞。
- Google Takeout 离线导入：`openbiliclaw import-youtube <path>` 解析 `.zip` 或解压目录，把历史数据转换为统一事件。
- discovery 策略：`yt_search` / `yt_trending` / `yt_channel` 使用真实 YouTube 抓取结果进入 LLM 打分过滤，产出 `source_platform="youtube"` 的 `DiscoveredContent`。
- 后台 discovery producer：`YoutubeDiscoveryProducer` 在 YouTube 平台族低于候选池 quota 时独立 tick，不再由主 refresh replenishment plan inline 调度。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| YouTube bootstrap 队列 | ✅ | `YtTaskQueue` 管理 `yt_tasks` 表，支持 pending / in_progress / completed / failed 状态、每日预算、过期 pending 清理和近期任务复用 |
| 扩展任务回写 | ✅ | 后端 `/api/sources/yt/task-result` 接收 `partial` / `ok` / `empty` / `failed`，单任务内合并去重，并通过 `source_bootstrap_state.json` 跳过跨任务旧条目 |
| init 集成 | ✅ | `init --yes-youtube` 在抖音 collect 完成后才入队，避免多个前台 tab 任务争抢焦点 |
| 单源 smoke | ✅ | `fetch-youtube` 独立验证扩展、登录态和后端任务桥，不隐式重建画像 |
| Takeout 导入 | ✅ | `import-youtube` 支持 Google Takeout `.zip` 或目录，JSON / HTML watch history、subscriptions CSV、liked videos CSV |
| 统一事件转换 | ✅ | `yt_history -> view`、`yt_subscriptions -> follow`、`yt_likes -> like`，全部携带 `metadata.source_platform="youtube"` |
| `yt_search` discovery | ✅ | LLM 从真实画像生成 YouTube 搜索关键词，`scrapetube` 拉搜索结果，再进入 LLM 相关性打分 |
| `yt_trending` discovery | ✅ | 优先通过 YouTube InnerTube browse API 拉 trending feed；当前 `FEtrending` 返回 400 时会降级抓取 YouTube 公开 topic 页（gaming / sports / news / podcasts / live）的 `ytInitialData` 视频，再进入 LLM 过滤 |
| `yt_channel` discovery | ✅ | 从 DB 读取 `event_type=follow` 且 `metadata.source_platform="youtube"` 的订阅频道，优先 `scrapetube`，频道 handle URL 走 `yt-dlp` fallback 拉最新视频 |
| 后台 discovery producer | ✅ | `YoutubeDiscoveryProducer` 独立调度 `yt_search` / `yt_trending` / `yt_channel`，按 `min_interval_minutes` 与每日执行 ledger 控制频率和预算 |
| 推荐点击回写 | ✅ | YouTube 推荐卡片打开时会把 `content_id / content_url / source_platform` 传给 `/api/recommendation-click`，事件和强画像信号保留 YouTube URL，不再退化成 B 站链接 |

## 公开 API

```python
from openbiliclaw.sources.yt_tasks import YtTaskQueue, yt_bootstrap_items_to_events
from openbiliclaw.youtube.takeout import parse_takeout
from openbiliclaw.discovery.strategies.youtube import YoutubeSearchStrategy
from openbiliclaw.youtube.client import YtScraperClient

queue = YtTaskQueue(database)
task_id = queue.enqueue_with_id(
    "bootstrap_profile",
    {
        "scopes": ["yt_history", "yt_subscriptions", "yt_likes"],
        "max_items_per_scope": 300,
        "max_scroll_rounds": 10,
    },
)

events = yt_bootstrap_items_to_events(
    [
        {
            "scope": "yt_history",
            "title": "How transformers work",
            "channel": "3Blue1Brown",
            "video_id": "abc1234defg",
            "url": "https://www.youtube.com/watch?v=abc1234defg",
        }
    ]
)

takeout = parse_takeout("~/Downloads/takeout.zip")

yt_search = YoutubeSearchStrategy(
    client=YtScraperClient(),
    llm_service=llm_service,
)
```

后台 producer 通常由 `RuntimeContext` 构造：

```python
from openbiliclaw.runtime.youtube_producer import YoutubeDiscoveryProducer

producer: YoutubeDiscoveryProducer
result = await producer.produce_if_due(limit=20)
```

CLI/API 入口：

```bash
openbiliclaw init --yes-youtube
openbiliclaw init --no-youtube
OPENBILICLAW_NO_YOUTUBE=1 openbiliclaw init
openbiliclaw fetch-youtube --wait-seconds 240
openbiliclaw import-youtube ~/Downloads/takeout.zip --dry-run
```

后端 HTTP 端点：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/sources/yt/next-task` | GET | 扩展轮询下一条 pending YouTube 任务 |
| `/api/sources/yt/task-result` | POST | 扩展回写单个 scope 的 partial / final 结果 |
| `/api/sources/yt/kick` | POST | CLI 入队后唤醒 runtime-stream，触发扩展立即 poll |

## 配置项

| 配置 | 默认值 | 说明 |
|------|------:|------|
| `OPENBILICLAW_NO_YOUTUBE` | 空 | 设为 `1` 时强制跳过 YouTube init，即使命令行传了 `--yes-youtube` |
| `OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS` | `10` | 每个 YouTube scope 的最大滚动轮数 |
| `OPENBILICLAW_YT_BOOTSTRAP_MAX_ITEMS` | `300` | 每个 YouTube scope 最多采集条目数 |
| `OPENBILICLAW_YT_BOOTSTRAP_WAIT_SECONDS` | `240` | CLI 等待扩展完成 bootstrap 的默认秒数 |
| `OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS` | `6` | YouTube `bootstrap_profile` 近期任务复用窗口；设为 `0` 可关闭 |
| `sources.youtube.enabled` | `false` | 是否启用 YouTube steady-state discovery 和候选池 quota |
| `sources.youtube.daily_search_budget` | `6` | `yt_search` 每日搜索 query 执行预算 |
| `sources.youtube.daily_trending_budget` | `50` | `yt_trending` 每日热门候选抓取预算 |
| `sources.youtube.daily_channel_budget` | `10` | `yt_channel` 每日订阅频道选择预算 |
| `sources.youtube.min_interval_minutes` | `60` | YouTube producer 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都可检查执行 |

## 设计决策

- YouTube 默认在非交互式终端跳过。它需要浏览器登录态和前台 tab 滚动，脚本环境盲目启用容易抢焦点或得到 0 条信号。
- `OPENBILICLAW_NO_YOUTUBE=1` 优先级高于 `--yes-youtube`。环境变量用于机器级永久 opt-out，必须能覆盖脚本参数。
- YouTube 任务在抖音任务完成后才入队。两者都会打开前台 tab，串行执行能避免页面懒加载和焦点状态互相干扰。
- YouTube bootstrap 的任务表负责任务生命周期，`source_bootstrap_state.json` 负责跨任务事件去重；二者分离，保证原始 task result 仍可诊断，而旧条目不会重复进入画像更新。
- `yt_tasks` 只服务 bootstrap profile / smoke，不承载 steady-state discovery。日常补池由后端 `YoutubeDiscoveryProducer` 直接调用 YouTube strategies，并通过 `ContentDiscoveryEngine` 进入统一缓存、pool-source 统计和 embedding warmup 路径。
- Takeout 导入和扩展导入都走统一事件格式。下游 `analyze_events()`、`build_initial_profile()` 和 memory 层不需要理解 YouTube 原始文件或 DOM schema。
- discovery 输出统一使用 `source_platform="youtube"` 和 `content_id=<YouTube video id>`。`ContentDiscoveryEngine` 必须按跨源 content identity 去重和缓存，不能只按 B 站 `bvid`，否则多个 YouTube 候选会被空 `bvid` 合并成一条。
