# TikTok 模块（实验性）

## 概述

TikTok 模块把 TikTok 公开短视频接入 OpenBiliClaw 的 discovery 候选供给，对应 issue #88。模块现有**两个可切换的后端**，由 `[sources.tiktok].mode` 选择：

- **Web API 后端**（`sources/tiktok_web.py`，默认优先）：以 **访客身份**（guest identity）直连 TikTok 自家 Web API，请求经纯 Python 签名（X-Dynosaur / X-Gnarly）+ curl_cffi Chrome TLS 指纹发送。不登录、不读 Cookie（可选登录 Cookie 除外）、不依赖浏览器扩展、不下载视频。
- **yt-dlp 后端**（`sources/tiktok.py`）：轻量匿名元数据链路，`tiktok:user` / `tiktok:tag` 列表 extractor 当前上游失效（见下），单视频 `TikTok` extractor 仍可用，作为 `auto` 模式的兜底保留。

发现面（Web API 后端）：

- `/api/recommend/item_list/` — 匿名 For-You 推荐流（`tiktok_feed` 策略），同时是访客身份 bootstrap 铸 msToken 的路径
- `/api/user/detail/` + `/api/post/item_list/` — 创作者资料（handle → secUid）与作品列表（`tiktok_user` 策略）
- `/api/challenge/detail/` + `/api/challenge/item_list/` — 话题标签（tag → challenge id）与标签视频列表（`tiktok_tag` 策略）
- `/api/search/item/full/` — 关键词搜索（**访客身份被上游 gate，预留 client 方法，无策略**）

两个后端都不提供可用的匿名搜索：yt-dlp 没有 TikTok 搜索 extractor，Web API 搜索端点对访客身份返回空响应。统一关键词规划器产出的搜索词由 `tag_from_query()` 压缩成无空格 hashtag 后走话题标签通道。

## 访客身份机制

Web API 后端不伪造任何身份材料，身份生命周期如下：

1. **bootstrap**：首次请求前发一次匿名 `/api/recommend/item_list/`；响应的 `set-cookie` 会铸出**真实的 128 字符 `msToken`**（同时收集 `ttwid` 等 cookie）。`msToken` 必须真实或留空——伪造的 msToken 会导致所有响应被 gate（0 字节 body + `tt_orcas_res: 1`），因此本模块只使用 TikTok 自己签发的 token。
2. **签名**：每个请求用 vendor 的 `sources/tiktok_sign.py`（来源 Evil0ctal/Douyin_TikTok_Download_API，Apache-2.0，纯 stdlib）产出 `X-Dynosaur` / `msToken` / `X-Bogus` / `X-Gnarly` 四个签名参数；签名时的 `user_agent` 与发送 UA 完全一致（`browser_version` 参数同）。
3. **失效检测**：响应 0 字节 + `tt_orcas_res: 1` = 被 gate（身份/参数问题，**不是传输问题，不无脑重试**）。触发一次重新 bootstrap 后重试；仍被 gate 则该次调用降级为"后端不可用"（`auto` 模式下 router 回退 yt-dlp）。
4. **传输重试**：TikTok 边缘对数据中心 IP 做 TLS 指纹级重置（实测约 5/6 失败率），每次请求最多重试 5 次传输错误。
5. **可选登录 Cookie**：`cookie_env`（默认 `OPENBILICLAW_TIKTOK_COOKIE`）或 `data/tiktok_cookie.json` 提供登录 Cookie 后并入同一会话 jar，用于解锁关键词搜索与更高限额；不配置时访客身份是完整合法的运行模式。

请求构造要点（2026-10-03 spike 实测固化）：`aid=1988`、`device_platform=web_pc`、19 位随机 `device_id`（**缺失会导致 0 字节空响应**）、`region` / `priority_region` / `tz_name` 默认匹配东京出口（JP / Asia/Tokyo）；HTTP 层用 curl_cffi `impersonate="chrome"`；代理策略与 yt-dlp 后端一致（`outbound_ytdlp_proxy()`：`system` 继承环境、`direct` 强制直连、`custom` 固定代理）。

## 上游可用性（真实网络实测，2026-10-03）

东京数据中心代理出口，访客身份（无登录 Cookie）：

| 端点 | 访客可用性 | 说明 |
|------|-----------|------|
| `/api/recommend/item_list/` | ✅ | 8 条真实视频；响应铸真实 msToken（bootstrap 关键路径） |
| `/api/user/detail/` | ✅ | uniqueId → secUid、昵称等 |
| `/api/post/item_list/` | ✅ | 15 条真实视频；替代失效的 yt-dlp `tiktok:user` |
| `/api/challenge/detail/` + `item_list/` | ✅ | 20 条真实视频（~590KB）；替代失效的 yt-dlp `tiktok:tag` |
| `/api/search/item/full/` | ❌ 被 gate | 200 但 0 字节 + orcas=1（真实 msToken 亦然）；需登录 Cookie |
| yt-dlp `TikTok` 单视频 extractor | ✅ | 网页解析路径正常（yt-dlp stable 2026.08.19 / nightly 2026.09.27） |
| yt-dlp `tiktok:tag` / `tiktok:user` | ❌ 上游失效 | `No working app info is available`（yt-dlp 官方已标记 TikTok broken） |

边缘连接对抗：TLS 重置实测约 5/6 失败率，client 内置重试；住宅 IP 出口成功率高得多。

## mode 语义

`[sources.tiktok].mode`：

- `auto`（默认）：Web API 优先；单次调用的后端级失败（传输耗尽或重 bootstrap 后仍被 gate）回退 yt-dlp；连续 3 次后端失败后 web 后端停放至进程结束，全部由 yt-dlp 服务。**返回空列表不等于失败**（标签/用户可能真的没有内容），不触发回退。
- `web`：强制 Web API；失败返回空结果，不回退。curl_cffi 缺失时 producer 不装配（日志说明）。
- `ytdlp`：仅 yt-dlp（Web API 后端引入前的行为）；`tiktok_feed` 策略在该模式下无产出（yt-dlp 无 Feed 面）。

分发由 `TiktokRouterClient` 完成，它实现与 `TiktokClient` 相同的 async 接口，策略代码不感知后端差异。

## 合规说明

- 只读公开元数据，使用 TikTok 自己签发的访客身份；**绝不伪造 msToken**（伪造比没有更糟，会被整体 gate）。
- 不下载媒体、不抓登录态、不做账号画像导入。
- 频率由 producer 预算与节流控制：推荐流属高曝光面，`daily_feed_budget` 默认压到每日 3 次拉取；`min_interval_minutes` 控制两次执行最小间隔。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| Web API 后端 client | ✅ | `TiktokWebClient` + `TiktokWebIdentity`：访客身份 bootstrap / msToken 生命周期 / gate 检测与单次重 bootstrap / 传输重试；覆盖 feed / user detail / post list / challenge detail + list / search（预留）；阻塞 IO 全部跑 executor；secUid 与 challenge id 进程内缓存 |
| 请求签名（vendored） | ✅ | `sources/tiktok_sign.py`（Evil0ctal/Douyin_TikTok_Download_API, Apache-2.0，纯 stdlib，仅 ruff 适配性微调）产出 X-Dynosaur / X-Gnarly；行为测试固定输出结构与 msToken 透传契约，算法正确性由上游保证 |
| `tiktok_feed` discovery | ✅ | 匿名 For-You 推荐流，无配置依赖；LLM 评估路径与 tag/user 策略一致；高曝光面，`daily_feed_budget` 默认 3（1 单位 = 1 次拉取） |
| `tiktok_tag` / `tiktok_user` web 路径 | ✅ | 策略接受两种后端返回形态（yt-dlp dict / web `DiscoveredContent`），由 `_coerce_candidate` 归一；`TiktokRouterClient` 按 `mode` 分发并在 `auto` 下回退 |
| 可选登录 Cookie | ✅ | `sources/tiktok_auth.py`（env 优先、`data/tiktok_cookie.json` 兜底，仿 douyin_auth）；`PUT /api/config` 粘贴路由到数据文件；GET 只回脱敏预览 |
| yt-dlp 轻量 client | ✅ | `TiktokClient` 封装 `tiktok:user` / `tiktok:tag` flat-extract 与单视频 `TikTok` extract；阻塞调用全部跑在线程池 executor，永不下载媒体 |
| 条目归一化 | ✅ | `normalize_tiktok_video()`（yt-dlp 条目）与 `parse_tiktok_item()`（web itemStruct）产出等价 `DiscoveredContent`；毫秒 duration 启发式换算为秒，`source_metadata.tiktok_aweme_id` 保留稳定身份 |
| 后台 discovery producer | ✅ | `TiktokDiscoveryProducer` 镜像 YouTube producer：`tiktok_discovery_runs` 每日执行 ledger、`min_interval_minutes` 节流、`daily_feed_budget` / `daily_tag_budget` / `daily_user_budget`、pool 缺口门、统一 candidate pipeline 入队 |
| 平台族注册 | ✅ | `tiktok` 独立平台族（`requires_overseas_network=True`、`routed_by_network_mode=True`、host `tiktok.com`） |
| source-auth 契约 | ✅ | `auth_tiktok()` 可选凭据语义：无 Cookie 时与 YouTube 同形（公开源 · 无需登录）；配置 Cookie 后 `credential="present"`（origin env / data_file），`verify_method` 诚实保持 `"none"`（暂无 TikTok 探针） |
| 关键词规划器接入 | ✅ | `tiktok` 加入 `_PLANNER_PLATFORMS`；`tiktok_tag` 走 claim → 注入（压缩为 hashtag）→ used/failed 生命周期（P1.7），P1.8 keyword id 随候选传递 |
| 配置面 | ✅ | `TiktokSourceConfig`（`mode` / `cookie_env` / `daily_feed_budget` 等）+ `config.example.toml` + `[scheduler.pool_source_shares] tiktok` + API config GET/PUT（非法 `mode` 保存时拒绝）+ credentials 只读行 |
| 依赖 | ✅ | `curl-cffi>=0.15` 进入默认依赖（Chrome TLS 指纹；与 yt-dlp 可选依赖同包，版本天然兼容） |

## 公开 API

```python
from openbiliclaw.sources.tiktok import (
    TiktokClient,
    normalize_tiktok_handle,
    normalize_tiktok_tag,
    normalize_tiktok_video,
    tag_from_query,
)
from openbiliclaw.sources.tiktok_web import (
    TiktokRouterClient,
    TiktokWebClient,
    TiktokWebIdentity,
    parse_tiktok_item,
)
from openbiliclaw.sources.tiktok_auth import resolve_tiktok_cookie
from openbiliclaw.discovery.strategies.tiktok import (
    TiktokFeedStrategy,
    TiktokTagStrategy,
    TiktokUserStrategy,
)

identity = TiktokWebIdentity(proxy="http://127.0.0.1:7897")
web = TiktokWebClient(identity=identity)
items = await web.get_feed(limit=12)            # list[DiscoveredContent] | None
items = await web.get_user_videos("@creator")   # None = 后端不可用, [] = 无内容
items = await web.get_tag_videos("booktok")

router = TiktokRouterClient(web=web, ytdlp=TiktokClient(), mode="auto")
```

后台 producer 通常由 `RuntimeContext` 构造（`build_tiktok_discovery_producer`）：

```python
from openbiliclaw.runtime.tiktok_producer import TiktokDiscoveryProducer

producer: TiktokDiscoveryProducer
result = await producer.produce_if_due(limit=20)
```

行为说明：

- producer 对每个 strategy 的 raw items 调 `enqueue_candidates(..., source_context="tiktok_feed" / "tiktok_tag" / "tiktok_user")`，再由共享 pipeline 混源 batch 评估入池；API 装配会把评估所有权交给 `CandidateEvalCoordinator`。
- 返回 payload 的 `discovered` 是本轮入队候选量，不等同于可立即推荐的池内数量。
- TikTok 在海外；`[network].mode = "direct"` 时国内通常直连超时，设置页会渲染后端统一的海外网络提示。

## 配置项

| 配置 | 默认值 | 说明 |
|------|------:|------|
| `sources.tiktok.enabled` | `false` | 是否启用 TikTok steady-state discovery 和候选池配额；实验性，默认关闭 |
| `sources.tiktok.mode` | `"auto"` | 后端选择：`auto`（web 优先 + yt-dlp 回退）/ `web` / `ytdlp`；非法值保存时拒绝 |
| `sources.tiktok.cookie_env` | `"OPENBILICLAW_TIKTOK_COOKIE"` | 可选登录 Cookie 环境变量；兜底 `data/tiktok_cookie.json`；不配置时访客身份运行 |
| `sources.tiktok.tags` | `[]` | 话题标签策略的常驻 hashtag（不带 `#`） |
| `sources.tiktok.creators` | `[]` | 创作者策略跟踪的 handle（`@` 前缀可省略） |
| `sources.tiktok.daily_feed_budget` | `3` | `tiktok_feed` 每日拉取上限（1 单位 = 1 次拉取）；`0` = 不设每日上限 |
| `sources.tiktok.daily_tag_budget` | `0` | `tiktok_tag` 每日执行预算；`0` = 不设每日上限 |
| `sources.tiktok.daily_user_budget` | `0` | `tiktok_user` 每日执行预算；`0` = 不设每日上限 |
| `sources.tiktok.request_interval_seconds` | `2` | 预留的请求间隔配置位（与 YouTube 对齐） |
| `sources.tiktok.min_interval_minutes` | `3` | producer 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都可检查执行 |
| `scheduler.pool_source_shares.tiktok` | `1` | TikTok 平台族候选池占比 |

## 设计决策

- **为什么新增 Web API 后端**：yt-dlp 的 `tiktok:tag` / `tiktok:user` 列表 extractor 上游失效且短期无修复迹象；2026-10-03 spike 实测证明纯 Python 签名 + 访客身份可以打通 TikTok Web API 的 feed / 创作者 / 话题标签列表，覆盖与 yt-dlp 路径相同甚至更多（feed 是新增面）。yt-dlp 路径保留为 `auto` / `ytdlp` 模式的兜底，上游修复后仍可用。
- **Router 而不是双 client 注入策略**：`TiktokRouterClient` 实现与 `TiktokClient` 相同的 async 接口并按 `mode` 分发，策略只拿一个 client，改动最小；回退语义（`None` = 后端不可用、`[]` = 无内容）集中在 router 一处。
- **web client 直接产 `DiscoveredContent`**：itemStruct 字段齐全（id / desc / createTime / author / stats / video），`parse_tiktok_item()` 一次映射到位；策略侧用 `_coerce_candidate` 兼容 yt-dlp dict 形态，单一策略代码服务两个后端。
- **没有搜索策略**：访客身份搜索被上游 gate（实测），yt-dlp 亦无搜索 extractor；`search_videos()` 仅作 client 预留（配置登录 Cookie 后可用），不进 planner、不做策略。
- **feed 预算按"次"计费**：推荐流一次拉取即一批候选，`daily_feed_budget` 计拉取次数而非条目数，默认 3 次/天压低高曝光面。
- **签名 vendor 而非自研**：复用上游已逐字节验证过的纯 Python 实现（Apache-2.0 兼容 MIT），文件头注明来源与修改；算法正确性测试归上游，本仓只测调用契约。
- **`tiktok` 拆出 douyin 族**：过去 `tiktok` 是 douyin 平台族别名，URL / 平台归属会把 tiktok.com 错记到 douyin。拆分后两族的配额、海外网络提示和事件归因各自独立。
- **不做的事**：账号行为采集、个性化登录态 Feed、视频下载、`recommendation/storage` 的 bvid 遗留路径、浏览器扩展改动，全部明确不在本模块范围。
