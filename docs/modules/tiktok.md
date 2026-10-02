# TikTok 模块（实验性）

## 概述

TikTok 模块把 TikTok 公开短视频接入 OpenBiliClaw 的 discovery 候选供给，对应 issue #88。实现刻意走**轻量 yt-dlp 路线**：不登录、不读 Cookie、不依赖浏览器扩展、不做个性化 Feed、不下载视频，只读取公开元数据。

发现面以 yt-dlp 的 TikTok extractor 为准：

- `tiktok:user` — 创作者公开视频列表（`https://www.tiktok.com/@<handle>`）
- `tiktok:tag` — 话题标签视频列表（`https://www.tiktok.com/tag/<tag>`）
- `TikTok` — 单视频元数据（`https://www.tiktok.com/@<handle>/video/<id>`，含 `vm.tiktok.com` 短链）

yt-dlp **没有** TikTok 搜索 extractor，因此本模块不提供搜索策略：统一关键词规划器产出的搜索词由 `tag_from_query()` 压缩成无空格 hashtag 后走话题标签通道。

## 上游可用性（真实网络实测，2026-10-03）

本模块在合并前做过真实请求验证（东京数据中心代理出口，yt-dlp stable 2026.08.19 与 nightly 2026.09.27）：

- **单视频 `TikTok` extractor 可用**：网页解析路径工作正常，能取回完整元数据（标题 / 作者 / 时长 / 播放数 / 点赞数）。
- **`tiktok:tag` / `tiktok:user` 列表 extractor 当前上游失效**：两个版本均报 `No working app info is available`（yt-dlp 官方已把 TikTok 标记为 broken），即 TikTok 移动 App API 的硬编码 app info 组合全部失效。**这意味着 `tiktok_tag` / `tiktok_user` 两个发现策略在上游修复前会持续返回空结果**——client 会吞掉异常返回空列表，discovery 只是无候选产出，不会报错或崩溃。
- **边缘连接对抗**：TikTok 边缘对数据中心 IP 做 TLS 指纹级重置（实测约 5/6 的连接被重置），因此 client 内置了 `retries` / `extractor_retries`；住宅 IP 出口下成功率会高得多。

维护策略：跟随 yt-dlp 版本升级（`yt-dlp>=2024.1.0` 的约束不变，上游修复后 bump 即可恢复列表发现），不为 TikTok 单独引入签名逆向或浏览器链路。升级 yt-dlp 后可用 `yt-dlp --flat-playlist "https://www.tiktok.com/tag/<tag>"` 验证列表 extractor 是否恢复。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| yt-dlp 轻量 client | ✅ | `TiktokClient` 封装 `tiktok:user` / `tiktok:tag` flat-extract 与单视频 `TikTok` extract；阻塞调用全部跑在线程池 executor，永不下载媒体；`[network].mode` 经 `outbound_ytdlp_proxy()` 生效 |
| 条目归一化 | ✅ | `normalize_tiktok_video()` 把 yt-dlp flat/full 条目映射为 `source_platform="tiktok"` 的 `DiscoveredContent`（aweme id / 标题 / 作者 / 封面 / 互动数 / 发布时间），毫秒级 duration 启发式换算为秒，`source_metadata.tiktok_aweme_id` 保留稳定身份 |
| `tiktok_tag` discovery | ✅ | LLM 从画像生成 hashtag（或 unified planner 注入搜索词 → 压缩为 hashtag）+ `[sources.tiktok].tags` 常驻标签，逐个走 `tiktok:tag` 列表，跨标签按 aweme id 去重后再进 LLM 评估 |
| `tiktok_user` discovery | ✅ | 读取 `[sources.tiktok].creators` 配置的 handle，逐个走 `tiktok:user` 最近视频，去重后评估 |
| 后台 discovery producer | ✅ | `TiktokDiscoveryProducer` 镜像 YouTube producer：`tiktok_discovery_runs` 每日执行 ledger、`min_interval_minutes` 节流（重启后经共享 cadence ledger 保持）、`daily_tag_budget` / `daily_user_budget`、pool 缺口门、统一 candidate pipeline 入队 |
| 平台族注册 | ✅ | `tiktok` 从 douyin 族别名中拆出成为独立平台族（`requires_overseas_network=True`、`routed_by_network_mode=True`、host `tiktok.com`）；`douyin` 族别名只剩 `douyin` / `dy` |
| source-auth 契约 | ✅ | `auth_tiktok()` 与 YouTube 同形：`auth_required=False` + `verify_method="none"`（公开源 · 无需登录），设置页状态芯片与 `/api/sources/status` 自动覆盖 |
| 关键词规划器接入 | ✅ | `tiktok` 加入 `_PLANNER_PLATFORMS` 平台表、query style 与 prompt 供给优势表；flag 开启时 `tiktok_tag` 走 claim → 注入 → used/failed 生命周期（P1.7 fetch-only），P1.8 keyword id 随候选传递 |
| 配置面 | ✅ | `TiktokSourceConfig` + `config.example.toml` `[sources.tiktok]` 段 + `[scheduler.pool_source_shares] tiktok` 配额 + API config GET/PUT（`TiktokSourceConfigOut`）+ credentials 只读行 |

## 公开 API

```python
from openbiliclaw.sources.tiktok import (
    TiktokClient,
    normalize_tiktok_handle,
    normalize_tiktok_tag,
    normalize_tiktok_video,
    tag_from_query,
)
from openbiliclaw.discovery.strategies.tiktok import TiktokTagStrategy, TiktokUserStrategy

client = TiktokClient()
entries = await client.get_tag_videos("booktok", limit=15)
entries = await client.get_user_videos("@creator", limit=10)
info = await client.get_video_metadata(
    "https://www.tiktok.com/@creator/video/7234567890123456789"
)
```

后台 producer 通常由 `RuntimeContext` 构造（`build_tiktok_discovery_producer`）：

```python
from openbiliclaw.runtime.tiktok_producer import TiktokDiscoveryProducer

producer: TiktokDiscoveryProducer
result = await producer.produce_if_due(limit=20)
```

行为说明：

- 与 YouTube 相同，producer 对每个 strategy 的 raw items 调 `enqueue_candidates(..., source_context="tiktok_tag" / "tiktok_user")`，再由共享 pipeline 混源 batch 评估入池；API 装配会把评估所有权交给 `CandidateEvalCoordinator`。
- 返回 payload 的 `discovered` 是本轮入队候选量，不等同于可立即推荐的池内数量。
- TikTok 在海外；`[network].mode = "direct"` 时国内通常直连超时，设置页会渲染后端统一的海外网络提示。

## 配置项

| 配置 | 默认值 | 说明 |
|------|------:|------|
| `sources.tiktok.enabled` | `false` | 是否启用 TikTok steady-state discovery 和候选池配额；实验性，默认关闭 |
| `sources.tiktok.tags` | `[]` | 话题标签策略的常驻 hashtag（不带 `#`），画像 / 规划器生成的标签在此之上叠加 |
| `sources.tiktok.creators` | `[]` | 创作者策略跟踪的 handle（`@` 前缀可省略） |
| `sources.tiktok.daily_tag_budget` | `0` | `tiktok_tag` 每日执行预算；`0` = 不设每日上限 |
| `sources.tiktok.daily_user_budget` | `0` | `tiktok_user` 每日执行预算；`0` = 不设每日上限 |
| `sources.tiktok.request_interval_seconds` | `2` | 预留的请求间隔配置位（与 YouTube 对齐） |
| `sources.tiktok.min_interval_minutes` | `3` | producer 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都可检查执行 |
| `scheduler.pool_source_shares.tiktok` | `1` | TikTok 平台族候选池占比 |

## 设计决策

- **为什么走 yt-dlp 而不是扩展**：issue #88 只要内容源；TikTok 公开列表 yt-dlp 已能匿名解析，引入扩展任务桥会把重型浏览器链路的运维成本（登录态、选择器漂移、MV3 租约）带进来却换不到增量信号。后续如需账号画像导入，再按 YouTube 的扩展 bootstrap 模式单独评估。
- **没有搜索策略**：yt-dlp 没有 `tiktok:search` extractor（2026.03.17 核实），抖音式的关键词搜索在 TikTok 侧不存在轻量实现；关键词统一映射为 hashtag，由 `tag_from_query()` 做空格压缩，规划器词的生命周期（claim → used / failed）与 YouTube `yt_search` 完全同构。
- **`tiktok` 拆出 douyin 族**：过去 `tiktok` 是 douyin 平台族别名，URL / 平台归属会把 tiktok.com 错记到 douyin。拆分后两族的配额、海外网络提示和事件归因各自独立。
- **不做的事**：登录态 / Cookie 抓取、个性化 Feed、视频下载、`recommendation/storage` 的 bvid 遗留路径、浏览器扩展改动，全部明确不在本模块范围。
