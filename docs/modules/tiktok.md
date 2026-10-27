# TikTok 模块

交付状态：已随 v0.3.229 发布，v0.3.231 起正式可用、默认关闭。插件默认声明 TikTok 站点访问权限，保存来源设置无需另行授权。公开发现与登录后关键词搜索已有实测；非空真实账号画像、周期增量和 Firefox 登录态仍待验，站内收藏暂缓。发布状态与能力验收分开记录，最新证据见 [验收台账](../platform-source-acceptance.tiktok.md)。

## 概述

TikTok 模块把 TikTok 公开短视频接入 OpenBiliClaw 的 discovery 候选供给，对应 issue #88。模块现有**两个可切换的后端**，由 `[sources.tiktok].mode` 选择：

- **Web API 后端**（`sources/tiktok_web.py`，默认优先）：以 **访客身份**（guest identity）直连 TikTok 自家 Web API，请求经纯 Python 签名（X-Dynosaur / X-Gnarly）+ curl_cffi Chrome TLS 指纹发送。不登录、不读 Cookie（可选登录 Cookie 除外）、不依赖浏览器扩展、不下载视频。
- **yt-dlp 后端**（`sources/tiktok.py`）：轻量匿名元数据链路，`tiktok:user` / `tiktok:tag` 列表 extractor 当前上游失效（见下），单视频 `TikTok` extractor 仍可用，作为 `auto` 模式的兜底保留。

发现面（Web API 后端）：

- `/api/recommend/item_list/` — 匿名 For-You 推荐流（`tiktok_feed` 策略），同时是访客身份 bootstrap 铸 msToken 的路径
- `/api/user/detail/` + `/api/post/item_list/` — 创作者资料（handle → secUid）与作品列表（`tiktok_user` 策略）
- `/api/challenge/detail/` + `/api/challenge/item_list/` — 话题标签（tag → challenge id）与标签视频列表（`tiktok_tag` 策略）
- `/api/search/item/full/` — 关键词搜索（访客身份被上游 gate；`tiktok_search` 策略按凭据挂载——配置登录 Cookie 且 mode 允许 web 时才存在，默认不启用）

两个后端都不提供可用的匿名搜索：yt-dlp 没有 TikTok 搜索 extractor，Web API 搜索端点对访客身份返回空响应。统一关键词规划器产出的搜索词有两个消费方：搜索策略挂载（配置登录 Cookie）时原样喂 `tiktok_search`（多词短语是搜索相对 hashtag 的核心价值），此时 `tiktok_tag` 退回常驻 tags + LLM 自生成；未挂载时词由 `tag_from_query()` 压缩成无空格 hashtag 后喂 `tiktok_tag`。

## 访客身份机制

Web API 后端不伪造任何身份材料，身份生命周期如下：

1. **bootstrap**：首次请求前发一次匿名 `/api/recommend/item_list/`；响应的 `set-cookie` 会铸出**真实的 128 字符 `msToken`**（同时收集 `ttwid` 等 cookie）。`msToken` 必须真实或留空——伪造的 msToken 会导致所有响应被 gate（0 字节 body + `tt_orcas_res: 1`），因此本模块只使用 TikTok 自己签发的 token。
2. **签名**：每个请求用 vendor 的 `sources/tiktok_sign.py`（来源 Evil0ctal/Douyin_TikTok_Download_API，Apache-2.0，纯 stdlib）产出 `X-Dynosaur` / `msToken` / `X-Bogus` / `X-Gnarly` 四个签名参数；签名时的 `user_agent` 与发送 UA 完全一致（`browser_version` 参数同）。
3. **失效检测**：响应 0 字节 + `tt_orcas_res: 1` = 被 gate（身份/参数问题，**不是传输问题，不无脑重试**）。触发一次重新 bootstrap 后重试；仍被 gate 则该次调用降级为"后端不可用"（`auto` 模式下 router 回退 yt-dlp）。
4. **传输重试**：TikTok 边缘对数据中心 IP 做 TLS 指纹级重置（实测约 5/6 失败率），每次请求最多重试 5 次传输错误。
5. **可选登录 Cookie**：`cookie_env`（默认 `OPENBILICLAW_TIKTOK_COOKIE`）或 `data/tiktok_cookie.json` 提供登录 Cookie 后并入同一会话 jar，用于尝试登录态关键词搜索（会话有效不证明搜索可用，也不承诺更高限额）；不配置时访客身份是完整合法的运行模式。已配置的 Cookie 可通过 `/passport/token/beat/web/` 主动探针验证（无签名、仅带 cookie 的会话心跳），写入门面（`PUT /api/config` 与 `POST /api/sources/tiktok/credential`）在保存前都会先过该探针，验证不通过不落盘。**浏览器插件自动同步**：安装了扩展的用户只需在浏览器登录 tiktok.com，service worker 检测到 sessionid 家族 Cookie 后会把完整 jar 推送到统一凭据端点（与手动粘贴同一验证强度）；未登录 / 登出时静默跳过（访客身份照常工作），无任何报错打扰。
6. **地区参数**：`region` / `tz_name`（默认 `JP` / `Asia/Tokyo`）随每个请求发送，应匹配代理出口地区；被风控 gate（空响应）时首先检查这两项。

请求构造要点（2026-10-03 spike 实测固化）：`aid=1988`、`device_platform=web_pc`、19 位随机 `device_id`（**缺失会导致 0 字节空响应**）、`region` / `priority_region` / `tz_name` 默认匹配东京出口（JP / Asia/Tokyo）；HTTP 层用 curl_cffi `impersonate="chrome"`；代理策略与 yt-dlp 后端一致（`outbound_ytdlp_proxy()`：`system` 继承环境、`direct` 强制直连、`custom` 固定代理）。

## 上游可用性（真实网络实测，2026-10-03）

东京数据中心代理出口，访客身份（无登录 Cookie）：

| 端点 | 访客可用性 | 说明 |
|------|-----------|------|
| `/api/recommend/item_list/` | ✅ | 8 条真实视频；响应铸真实 msToken（bootstrap 关键路径） |
| `/api/user/detail/` | ✅ | uniqueId → secUid、昵称等 |
| `/api/post/item_list/` | ✅ | 15 条真实视频；替代失效的 yt-dlp `tiktok:user` |
| `/api/challenge/detail/` + `item_list/` | ✅ | 20 条真实视频（~590KB）；替代失效的 yt-dlp `tiktok:tag` |
| `/api/search/item/full/` | ❌ 访客被 gate | 200 但 0 字节 + orcas=1（真实 msToken 亦然）；需登录 Cookie，已按条件挂载 `tiktok_search` 策略（默认不启用） |
| `/passport/token/beat/web/` | ✅（登录 cookie 探针） | 无签名、仅带 cookie 的会话心跳；用于验证可选登录 Cookie 是否存活（`probe_tiktok_login`） |
| yt-dlp `TikTok` 单视频 extractor | ✅ | 网页解析路径正常（yt-dlp stable 2026.08.19 / nightly 2026.09.27） |
| yt-dlp `tiktok:tag` / `tiktok:user` | ❌ 上游失效 | `No working app info is available`（yt-dlp 官方已标记 TikTok broken） |

边缘连接对抗：TLS 重置实测约 5/6 失败率，client 内置重试；住宅 IP 出口成功率高得多。

## mode 语义

`[sources.tiktok].mode`：

- `auto`（默认）：Web API 优先；单次调用的后端级失败（传输耗尽或重 bootstrap 后仍被 gate）回退 yt-dlp；连续 3 次后端失败后 Web 后端冷却 60 秒，随后自动恢复探测。**返回空列表不等于失败**（标签/用户可能真的没有内容），不触发回退。
- `web`：强制 Web API；失败保留错误状态，包括熔断期间也不回退。curl_cffi 缺失时 producer 不装配（日志说明）。
- `ytdlp`：仅 yt-dlp（Web API 后端引入前的行为）；`tiktok_feed` 策略在该模式下无产出（yt-dlp 无 Feed 面）。

分发由 `TiktokRouterClient` 完成，它实现与 `TiktokClient` 相同的 async 接口，策略代码不感知后端差异。

## 账号信号 bootstrap 与周期回拉（浏览器任务）

除公开 discovery 外，TikTok 按 `docs/platform-source-contract.tiktok.toml`（`integration_level="full"`）接入账号行为信号：点赞（`tt_like`）、收藏（`tt_collect`）、观看历史（`tt_history`）、关注（`tt_follow`）四种 scope 均由浏览器扩展在用户已登录的 tiktok.com 标签页内只读执行（`extension:browser-task:session-read-only`），后端绝不重放 TikTok 凭据。

后端基建在 `sources/tiktok_tasks.py`，镜像抖音实现但完全独立：

- `TiktokTaskQueue` 管理独立的 `tiktok_tasks` SQLite 表（pending → in_progress → completed/failed），`BEGIN IMMEDIATE` 内完成 stale-lease 重领、source-wide single-flight 与 claim；每日预算与 15 分钟 lease 语义与抖音一致。
- scope→事件映射：点赞→`like`、收藏→`favorite`、观看历史→`view`、关注→`follow`；信号强度 collect=1.0 / like=0.85 / follow=0.6（同抖音），history=0.35（被动曝光，沿用 YouTube `yt_history` 先例）。
- 任务结果走统一两阶段 staged completion（`task_result_protocol.py` 已注册 `tiktok_tasks`）：先在单事务冻结第一份 canonical payload + staged marker，再重放事件入口与 seen-key 投影，最后只翻 terminal status。
- API 路由：`GET /api/sources/tiktok/next-task`（无任务 bodyless 204）、`POST /api/sources/tiktok/task-result`、`POST /api/sources/tiktok/kick`。
- 周期增量已注册进 `runtime/source_incremental_sync.py` 五张表（`SOURCE_ORDER` / `_TASK_SPECS` / `_SOURCE_CONFIG_ALIASES` / `_SOURCE_CONFIG_NAMES` / `_SOURCE_INTERVAL_FIELDS`），由 `scheduler.source_incremental_enabled`（默认 `false`）与 `sources.tiktok.incremental_enabled`（默认 `false`）双重门控，周期字段为 `scheduler.tiktok_incremental_hours`（缺省继承全局）。
- seen-key 去重投影写入 `source_bootstrap_state.json` 的 `tiktok_seen_item_keys`；guided init 成功（`ok`）会 seed 增量调度时钟，`empty` 与抖音一样视为证据不足不 seed。

## Guided init 接入

TikTok 是 guided init 的正式来源（`guidedInit: true`）：

- **准入**：`_INIT_SOURCE_ORDER` 与 `init_prereqs._PLATFORM_SOURCE_FIELDS` 均含 `tiktok`；`POST /api/init` 的显式 `sources` 选择可只选 tiktok（不再归一化为空）。前端 setup / 桌面 / popup 的初始化来源清单由 `web/shared/source-status.js` 的 `SOURCE_CAPABILITIES` 派生，无需逐页注册。
- **CLI**：`openbiliclaw init --yes-tiktok` 跳过提问直接启用，`--no-tiktok` 跳过；`OPENBILICLAW_NO_TIKTOK=1` 优先级最高（覆盖 `--yes-tiktok`）。非交互终端默认不启用。交互模式会展示扩展安装 / 登录 tiktok.com / 后台任务标签页的准备清单。
- **采集链**：init 第 1 阶段在 Instagram 之后串行调用 `_enqueue_tiktok_bootstrap_task`（`enqueue_tiktok_bootstrap` + kick）与 `_collect_tiktok_bootstrap_events`（轮询 `tiktok_tasks` 行直到 terminal，默认等待 480 秒，`OPENBILICLAW_TIKTOK_BOOTSTRAP_WAIT_SECONDS` 可调）；终态标签为 `ok / degraded / empty / timeout / failed / skipped`，`degraded` 保留已采事件并按部分完成上报。
- **画像链**：任务结果由 task-result 路由负责持久化与事件入口重放（init-owned 任务的画像传播不被 init 围栏跳过）；本 run 的偏好分析与画像构建直接使用收集到的事件，经 `_tiktok_events_to_history_items` 转成画像构建行（`source_platform=tiktok`）。
- **调度种子**：`ok` 终态 seed `last_attempt_at` / `last_success_at`（`seed_guided_init_attempts` 的 ok-only 组），`empty` / `degraded` / `failed` 不推进增量时钟。

## 站内收藏（native save）

TikTok 加入七平台扩展保存矩阵（`docs/modules/saved-sync.md`）：本地「收藏 / 稍后再看」先落 membership（local-first），开启自动同步或手动 `/sync` 时由 `ExtensionNativeSaveBroker` 把 job 写入 `extension_native_save_jobs` ledger，扩展 `tiktok-task-dispatcher` 经 `GET /api/sources/tiktok/next-task` 优先领取 native-save job，在已登录 tiktok.com 标签页内执行收藏点击（`extension/src/content/native-save/tiktok.ts`，目标 `TikTok Favorites`；watch-later 按能力矩阵回退 favorite）。

- 自动同步默认关闭（`auto_sync_enabled = false`）；真实账号写入只允许在 `allow_state_changing=true` 且 exact platform/action/content ID/expected target 精确命名授权下运行（授权 E2E runbook 同一流程）。
- `is_native_save_local_only("tiktok")` 已为 `false`；executor 的 `unsupported_content_type` 等内容限制仍落 local-only 终态。
- 就绪证据复用凭据路径：`auth_tiktok` 的 capability map（`_TIKTOK_CAPABILITY_AUTH_MODES`）把 `profile / bootstrap / incremental / native-save` 标为 `login-required`，readiness 由同步 Cookie + passport 心跳探针决定；guided init 对 tiktok-only 且未就绪的选择返回 `no_profile_signal_sources`，混合选择降级并保留公开发现。

## 合规说明

- 只读公开元数据，使用 TikTok 自己签发的访客身份；**绝不伪造 msToken**（伪造比没有更糟，会被整体 gate）。
- 不下载媒体、不抓登录态；账号行为信号只在用户浏览器会话内只读观察，后端不接触 Cookie 值。
- 频率由 producer 预算与节流控制：推荐流属高曝光面，`daily_feed_budget` 默认压到每日 3 次拉取；`min_interval_minutes` 控制两次执行最小间隔。
- **登录态关键词搜索有账号风险**：`tiktok_search` 使用用户自己的登录 Cookie 调用搜索端点，这类登录态抓取违反 TikTok ToS，可能导致账号被限制。因此该策略默认不启用——只有配置了登录 Cookie 且 mode 允许 web 时才挂载，预算同样压低（`daily_search_budget` 默认 3）。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| Web API 后端 client | ✅ | `TiktokWebClient` + `TiktokWebIdentity`：访客身份 bootstrap / msToken 生命周期 / gate 检测与单次重 bootstrap / 传输重试；覆盖 feed / user detail / post list / challenge detail + list / search（预留）；阻塞 IO 全部跑 executor；secUid 与 challenge id 进程内缓存 |
| 请求签名（vendored） | ✅ | `sources/tiktok_sign.py`（Evil0ctal/Douyin_TikTok_Download_API, Apache-2.0，纯 stdlib，仅 ruff 适配性微调）产出 X-Dynosaur / X-Gnarly；行为测试固定输出结构与 msToken 透传契约，算法正确性由上游保证 |
| `tiktok_feed` discovery | ✅ | 匿名 For-You 推荐流，无配置依赖；LLM 评估路径与 tag/user 策略一致；高曝光面，`daily_feed_budget` 默认 3（1 单位 = 1 次拉取） |
| `tiktok_tag` / `tiktok_user` web 路径 | ✅ | 策略接受两种后端返回形态（yt-dlp dict / web `DiscoveredContent`），由 `_coerce_candidate` 归一；`TiktokRouterClient` 按 `mode` 分发并在 `auto` 下回退 |
| 可选登录 Cookie | ✅ | `sources/tiktok_auth.py`（env 优先、`data/tiktok_cookie.json` 兜底，仿 douyin_auth）；`PUT /api/config` 与统一凭据端点粘贴都先过探针再落盘；GET 只回脱敏预览 |
| Cookie 主动探针 | ✅ | `probe_tiktok_login()` 调 `/passport/token/beat/web/`（无签名、仅 cookie）；verified / failed / indeterminate 三态映射（传输失败与风控 gate 绝不误判为失效）；`VERIFY_ACTIONS["tiktok"]="live_probe"` |
| 凭据写入门面 | ✅ | `CREDENTIAL_SPECS["tiktok"]`：结构门要求 sessionid / sessionid_ss / sid_tt 至少其一，live gate 接 passport 探针；store 落 `data/tiktok_cookie.json` |
| 插件 Cookie 自动同步 | ✅ | `cookie-sync.ts` 覆盖 tiktok.com：登录检测（sessionid 家族）→ 完整 jar → `/api/sources/tiktok/credential`；按平台防抖 + 小时 alarm + runtime-stream `tiktok_cookie_sync_requested`；登出静默；manifest host_permissions 加 `*://*.tiktok.com/*` |
| `tiktok_search` discovery | ✅（默认不启用） | 仅当配置登录 Cookie 且 mode 允许 web 时挂载；planner 注入词**原样使用**（不做 hashtag 压缩），LLM 画像兜底生成；搜索响应混合卡片只收视频条目（`require_video` 过滤）；`daily_search_budget` 默认 3 |
| 地区参数配置化 | ✅ | `region` / `tz_name` 配置项注入 `TiktokWebIdentity`，空值回退默认；应匹配网络出口地区 |
| yt-dlp 轻量 client | ✅ | `TiktokClient` 封装 `tiktok:user` / `tiktok:tag` flat-extract 与单视频 `TikTok` extract；阻塞调用全部跑在线程池 executor，永不下载媒体 |
| 条目归一化 | ✅ | `normalize_tiktok_video()`（yt-dlp 条目）与 `parse_tiktok_item()`（web itemStruct）产出等价 `DiscoveredContent`；毫秒 duration 启发式换算为秒，`source_metadata.tiktok_aweme_id` 保留稳定身份 |
| 后台 discovery producer | ✅ | `TiktokDiscoveryProducer` 镜像 YouTube producer：`tiktok_discovery_runs` 每日执行 ledger、`min_interval_minutes` 节流、`daily_feed_budget` / `daily_tag_budget` / `daily_user_budget`、pool 缺口门、统一 candidate pipeline 入队 |
| 平台族注册 | ✅ | `tiktok` 独立平台族（`requires_overseas_network=True`、`routed_by_network_mode=True`、host `tiktok.com`） |
| source-auth 契约 | ✅ | `auth_tiktok()` 可选凭据语义：无 Cookie 时与 YouTube 同形（公开源 · 无需登录，`verify_method="none"`）；配置 Cookie 后 `credential="present"` + `verify_method="live_probe"`（passport beat 探针）；capability map（`_TIKTOK_CAPABILITY_AUTH_MODES`）逐能力区分 discover（optional-credential，访客就绪）与 profile / bootstrap / incremental / native-save（login-required，随同步 Cookie + 探针 verdict 投影 ready / stale / login_required / unverified），guided init 据此做 bootstrap 准入 |
| 站内收藏（native save） | ✅ | `ExtensionAdapterDefinition("tiktok", ...)` 注册进七平台 broker；`extension/src/content/native-save/tiktok.ts` 在已登录标签页执行收藏（目标 `TikTok Favorites`，watch-later 回退 favorite）；自动同步默认关闭，真实写入需精确命名授权；已获指定视频授权；真实受控 E2E 未通过，刷新后未持久保存，见最新验收台账 |
| 关键词规划器接入 | ✅ | `tiktok` 加入 `_PLANNER_PLATFORMS`；claim 的词优先喂 `tiktok_search`（原词不压缩，search 挂载时），未挂载时喂 `tiktok_tag`（压缩为 hashtag）；used/failed 生命周期跟随实际消费词的策略（P1.7），P1.8 keyword id 随候选传递 |
| 头图 CDN 白名单 | ✅ | `tiktokcdn.com` / `tiktokcdn-us.com` / `tiktokcdn-eu.com` 进入 image_cache 头图白名单，走 `[network]` 海外路由（与 i.ytimg.com 同构，不进 CN 直连名单）；真实 For-You 封面经代理抓取 200 实测通过 |
| `discover --source tiktok` CLI | ✅ | 手动触发正式 `TiktokDiscoveryProducer`（`enabled_override` 旁路 daemon 总开关，镜像 douyin 分支形态） |
| 账号 bootstrap 任务队列 | ✅ | `TiktokTaskQueue` + `tiktok_tasks` 表（镜像抖音、完全独立）；`tt_like` / `tt_collect` / `tt_history` / `tt_follow` 四 scope，含仓库首个观看历史类 scope；两阶段 staged completion；`GET /api/sources/tiktok/next-task`、`POST /api/sources/tiktok/task-result`、`POST /api/sources/tiktok/kick` 三条路由；周期回拉注册进 `source_incremental_sync` 并由双重开关默认关闭 |
| 桌面 / 插件设置页卡片 | ✅ | desktop 设置页与 popup 各有 TikTok 卡片：启用开关、后端 mode、可选 Cookie（扩展同步 / 手动粘贴）、region / tz_name、tags / creators、四分支预算、节流、占比、「测试连接」（通用 verify 分发）与发布日期偏好 |
| guided init 接入 | ✅ | `guidedInit: true`：`_INIT_SOURCE_ORDER` 含 tiktok，init 第 1 阶段串行入队并收集 `bootstrap_profile` 任务（点赞 / 收藏 / 关注；历史仅显式任务可选），事件经 `tiktok_bootstrap_videos_to_events` 进入偏好分析与画像构建；CLI 有 `--yes-tiktok` / `--no-tiktok`（`OPENBILICLAW_NO_TIKTOK=1` 优先），setup / 桌面 / popup 的来源清单由共享 roster 派生 |
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
from openbiliclaw.sources.tiktok_web import probe_tiktok_login
from openbiliclaw.sources.tiktok_tasks import (
    TiktokTaskQueue,
    tiktok_bootstrap_video_key,
    tiktok_bootstrap_videos_to_events,
)
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
- TikTok 默认跟随 `[network].mode = "system"` 海外代理。Web API 与登录探针的 curl_cffi 会话显式读取 OS/环境代理（含 macOS 系统代理）；`direct` 用空 scheme 映射关闭 libcurl 环境继承，`custom` 固定代理并清除环境 NO_PROXY 绕过。封面沿用同一适配器；浏览器账号采集和站内收藏仍由 Chrome 自己的代理设置决定。
- `network.curl_cffi_proxy_kwargs(proxy)` 接受 `None`（OS/环境）、空串（直连）或 URL（固定代理），返回会话 kwargs；不改变全局环境、系统代理或 TLS 校验。`[network].mode = "direct"` 时国内通常直连超时，设置页会渲染后端统一的海外网络提示。

## 配置项

| 配置 | 默认值 | 说明 |
|------|------:|------|
| `sources.tiktok.enabled` | `false` | 是否启用 TikTok steady-state discovery 和候选池配额；默认关闭 |
| `sources.tiktok.incremental_enabled` | `false` | 扩展在线周期账号回拉的逐源开关；需同时开启 `scheduler.source_incremental_enabled` |
| `sources.tiktok.mode` | `"auto"` | 后端选择：`auto`（web 优先 + yt-dlp 回退）/ `web` / `ytdlp`；非法值保存时拒绝 |
| `sources.tiktok.cookie_env` | `"OPENBILICLAW_TIKTOK_COOKIE"` | 可选登录 Cookie 环境变量；兜底 `data/tiktok_cookie.json`；不配置时访客身份运行 |
| `sources.tiktok.region` | `"JP"` | Web API 请求的地区参数，应匹配网络出口地区；被风控 gate 时首先检查 |
| `sources.tiktok.tz_name` | `"Asia/Tokyo"` | Web API 请求的时区参数，与 `region` 配套 |
| `sources.tiktok.tags` | `[]` | 话题标签策略的常驻 hashtag（不带 `#`） |
| `sources.tiktok.creators` | `[]` | 创作者策略跟踪的 handle（`@` 前缀可省略） |
| `sources.tiktok.daily_feed_budget` | `3` | `tiktok_feed` 每日拉取上限（1 单位 = 1 次拉取）；`0` = 不设每日上限 |
| `sources.tiktok.daily_search_budget` | `3` | `tiktok_search` 每日关键词上限（1 单位 = 1 个关键词）；搜索需登录 Cookie，默认不启用 |
| `sources.tiktok.daily_tag_budget` | `0` | `tiktok_tag` 每日执行预算；`0` = 不设每日上限 |
| `sources.tiktok.daily_user_budget` | `0` | `tiktok_user` 每日执行预算；`0` = 不设每日上限 |
| `sources.tiktok.request_interval_seconds` | `2` | 实际请求最小间隔，包含 bootstrap 与重试；formal/inspiration 共享 SQLite 时隙 |
| `sources.tiktok.min_interval_minutes` | `3` | producer 两次执行之间的最小间隔；`0` 表示每个 refresh tick 都可检查执行 |
| `scheduler.pool_source_shares.tiktok` | `1` | TikTok 平台族候选池占比 |
| `scheduler.tiktok_incremental_hours` | `null` | 周期回拉逐源覆盖；缺省继承全局 `source_incremental_hours`，`0` 只关闭 TikTok，范围 `0..168` |

## 设计决策

- **为什么新增 Web API 后端**：yt-dlp 的 `tiktok:tag` / `tiktok:user` 列表 extractor 上游失效且短期无修复迹象；2026-10-03 spike 实测证明纯 Python 签名 + 访客身份可以打通 TikTok Web API 的 feed / 创作者 / 话题标签列表，覆盖与 yt-dlp 路径相同甚至更多（feed 是新增面）。yt-dlp 路径保留为 `auto` / `ytdlp` 模式的兜底，上游修复后仍可用。
- **Router 而不是双 client 注入策略**：`TiktokRouterClient` 实现与 `TiktokClient` 相同的 async 接口并按 `mode` 分发，策略只拿一个 client，改动最小；回退语义（`None` = 后端不可用、`[]` = 无内容）集中在 router 一处。
- **web client 直接产 `DiscoveredContent`**：itemStruct 字段齐全（id / desc / createTime / author / stats / video），`parse_tiktok_item()` 一次映射到位；策略侧用 `_coerce_candidate` 兼容 yt-dlp dict 形态，单一策略代码服务两个后端。
- **搜索策略按凭据挂载**：访客身份搜索被上游 gate（实测），因此 `tiktok_search` 只在"配置了登录 Cookie 且 mode 允许 web"时才由装配处挂载（router 的 `search_available` 判定），否则 producer 策略元组里根本没有它。登录态抓取违反 TikTok ToS、有账号风险，文档与配置注释都明确写出，默认不启用。planner claim 语义随挂载形态切换：search 挂载时 claim 的词原样喂 search（多词短语不被 hashtag 压缩浪费），tag 退回常驻 tags + LLM 自生成；search 未挂载时维持 claim → tag 压缩的旧语义。used/failed 标记跟随实际消费词的策略。
- **planner 词只喂一个策略**：同一份 claim 的词若同时喂 search 和 tag 会产生重复采集，且 used 语义无法归属；search 是词的无损消费者、tag 是有损（压缩）消费者，因此 search 优先。search 预算耗尽但 tag 仍有预算时自动回落 tag（`runnable` 判定）。
- **feed 预算按"次"计费**：推荐流一次拉取即一批候选，`daily_feed_budget` 计拉取次数而非条目数，默认 3 次/天压低高曝光面。
- **签名 vendor 而非自研**：复用上游已逐字节验证过的纯 Python 实现（Apache-2.0 兼容 MIT），文件头注明来源与修改；算法正确性测试归上游，本仓只测调用契约。
- **`tiktok` 拆出 douyin 族**：过去 `tiktok` 是 douyin 平台族别名，URL / 平台归属会把 tiktok.com 错记到 douyin。拆分后两族的配额、海外网络提示和事件归因各自独立。
- **不做的事**：账号行为采集、个性化登录态 Feed、视频下载、账号画像初始化和上游原生收藏不在本次 discovery-only 范围；扩展支持 Cookie 同步与设置展示。


## 2026-10-04 验收修复与运行状态

范围冻结为 `discovery-only`，见 [机器契约](../platform-source-contract.tiktok.toml) 与
[验收记录](../platform-source-acceptance.tiktok.md)。Cookie 心跳成功只证明会话仍有效，不能代替搜索端点的真实成功证据。

- `TiktokRequestError.reason` 区分 `rate_limited / login_required / upstream_error / invalid_response / unavailable / fallback_unavailable`；HTTP/业务错误、challenge、未知结构不再算正常空列表。失败查询保留诊断，成功查询候选不丢弃；producer 返回 `degraded` 或错误。
- `TiktokRequestState(path, interval_seconds)` 的 `before_request()`、`defer(seconds)`、`cooldown_remaining()` 供正式发现和 inspiration 共用。账本位于用户 data root 的 `tiktok_request_state.sqlite3`，只保存时间戳，不存 Cookie 或响应正文；跨进程原子保留请求时隙，429 冷却在进程重启后仍有效。
- 每日 search/tag 预算在领取关键词前约束数量。失败词标 failed、成功词标 used，未领取的词保留 pending；实际执行量不再截断后少记。feed 每次默认取 12 条，每日调用预算独立计数。
- 强制 `web` 永不进入 yt-dlp；`auto` 熔断 60 秒后可恢复。`ytdlp` 模式不调度不存在的 feed 分支。
- 收藏数同时映射 `favorite_count` 和兼容字段 `collect_count`，两种 transport 保持一致。

### 只读 smoke

```bash
openbiliclaw discover-tiktok --mode feed --limit 5
openbiliclaw discover-tiktok --mode tag --query science --limit 5
openbiliclaw discover-tiktok --mode search --query "machine learning" --limit 5
PYTHONPATH=src python scripts/smoke_tiktok_pipeline.py --config /path/to/config.toml
```

`discover-tiktok` 在临时目录保存请求状态，不写生产候选、记忆或画像；search 需要有效登录态。
完整 pipeline 脚本保留用户配置的模型与网络路线，以明确的合成 science 兴趣画像在隔离数据库中测试评估、入池和推荐 API。两者均只读上游。

### TikTok 封面网络边界

`runtime.tiktok_images.fetch_tiktok_cover(httpx.URL) -> (bytes, content_type)` 由共享 image cache 对三个 TikTok CDN 后缀分发。每跳经配置网络路由调用 Cloudflare DoH（仅 hostname），拒绝非公开/过渡地址、非白名单 host 和非 443 端口，以 CONNECT_TO 固定 IP 并保持 TLS 主机身份；使用新会话、无账号 Cookie、10MB 流式上限。DNS 失败时不回退到不安全下载。其他来源保留原 image cache 路径。

资料查找与列表采用相同错误原则：成功 envelope 缺少作者 secUid 或 challenge ID 时抛出 `invalid_response`，不会猜测为“用户/话题不存在”并返回空列表。

### 移动端范围

移动网页支持 TikTok 推荐卡、封面、HTTPS 打开与本地收藏/稍后再看。按共享接入规范 §0.3，来源设置、凭据管理和测试连接只在桌面 Web 与扩展 popup 提供；移动端这些入口明确不适用，由 TikTok contract 与契约测试记录。

### 登录态续验（2026-10-10）

已安装 Chrome 分支扩展自动同步 Cookie、真实 passport 验证与关键词搜索通过；正式登录搜索 producer 使用完整短语，4 条候选经实际模型评估全部入池并生成文案。完整证据及测试边界见 [验收记录](../platform-source-acceptance.tiktok.md#已安装-chrome-与登录搜索续验2026-10-10)。这不扩展 discovery-only 范围。

## TikTok full-source 修复验收（2026-10-11）

- 后端 `bootstrap_profile` wire 与插件多 scope 调度统一；`max_items_per_scope` 在扩展和后端累计合并时均冻结且最多 300；后端裁剪会持久保留 bounds_exceeded，并将最终 canonical status 与 staged marker 同时降级为 degraded。Python 实际 next-task → TypeScript dispatcher → API 入库集成测试覆盖四种事件及超额裁剪，不把 DOM fixture 当真实登录态证据。
- 采集只接受当前 viewer 本人 profile 的已确认 scope 列表；登录墙优先于旧登录标记。scope 之间固定账号，后端将 observed identity 规范化并散列为本地 account key；这不是上游 verified identity。account_key 固定使用规范化 handle 的 SHA-256；可选 secUid 只作为单向散列冲突守卫，字段暂缺或补齐不会改变同账号分区。画像首次绑定保存在 `auth_state.tiktok_profile_account_key` 与可选 `tiktok_profile_sec_uid_key`，切换账号返回 `tiktok_account_switch_requires_reset`，须使用新数据目录或显式重置后再导入，公开发现不受影响。
- 只有冻结 `profile_update=true`、incremental 或当前 init-owned 任务能传播画像事件。普通 smoke 仅写任务结果，不写画像、seen keys 或全局账号绑定。事件幂等键含 account key，任意容器字段不会被字符串化成作者/身份。
- scope 回报使用 durable FIFO；120 秒 idle 与最多 360 秒 absolute deadline 分别持久化，恢复/心跳不延长 idle；超时/后续 scope 失败保留既有 rows。task-tab sender 校验与整页导航恢复阻止串任务，默认周期同步仍关闭，任务标签保持后台。
- 收藏执行在等待与确认时重读当前视频 URL；SPA 切换不允许点击另一视频，也不把其选中态当成功。授权仍为单条 exact platform/action/content_id/target。
- 真实 Chrome 控制当前返回 `nodeRepl.fetch request failed`，没有可读标签页；本批四项账号能力的 live E2E 尚未完成。此前 #275 的搜索/Cookie E2E 仅证明发现链路，不替代本批验收。


### 扩展迟到的 Cookie 同步

后端先以访客模式启动、再收到扩展 Cookie 时，统一凭据入口在验证保存后安全重建运行时，替换捕获旧 Cookie 的 TikTok 客户端并挂载搜索。仅有效凭据变化触发重建；env 优先语义不变，相同 Cookie 重投不重启任务。2026-10-11 正常后端真实运行复现并修复该缺口，详见验收台账。

账号采集登录墙的文字兜底只读取 DOM `innerText`，不读取包含 SSR JSON、翻译表和隐藏模板的 `textContent`；没有可见登录墙也不代表已登录，仍需正向会话/本人身份依据。真实安装版诊断与隐藏文案回归见验收台账。

页面文档 complete 后仍等待最多 40 次、每次 250ms 的登录身份渲染；点击本人资料入口后同样有界等待 URL 与账号标题吻合，替代固定 2.5 秒延迟。未就绪仍失败，沿用任务总超时和账号边界。

已在本人 URL 时不再次点击资料入口，防止标题尚未渲染时整页导航反复恢复同一 scope；等待也检查墙钟上限，避免后台标签页定时器节流无限拉长轮数预算。

当前 TikTok 资料页可将昵称放在 `user-title`、账号名放在 `user-subtitle`。本人页面必须同时满足当前 URL 属于已观察 viewer，且可见主/副标题之一与该账号名精确匹配；不能把显示昵称当作账号键。

账号 scope 基础预算为 60 秒（包括登录 hydration、本人的资料页导航及恢复），再按滚动轮数追加；整任务上限仍为 360 秒、idle 为 120 秒，CLI 等待仍为 480 秒。

点击范围标签后同样有界等待已确认的 scope root 再采集；静态延迟不能代替列表渲染证明。


### 账号入口与初始化复用续修（2026-10-11）

| 已实现行为 | 边界 |
| --- | --- |
| 当前桌面版喜欢/收藏显式空态、本人关注空弹窗识别 | 要求本人身份、选中标签/弹窗标题及 scope 专属空文案；不将通用错误、空白或未加载页面当成 empty。此补丁不证明新版非空列表可用。 |
| 设置页 session viewer 解析 | 接受 canonical `webapp.app-context.user` 身份 claim，同时仍要求正向登录 DOM/显式登录标记；显式登出和导航身份冲突优先拒绝。 |
| 历史入口导航 | 使用可见头像/更多菜单、`settings-entrance`（本次中文标签为“通用”）进入实际 `/setting`；整页恢复不重开设置。当前测试账号未显示历史入口，仍明确报错。 |
| TikTok 初始化去重 | 比较 scope、profile_update、采集预算与 incremental owner；仅复用相同契约的在途任务或最新权威 ok/empty 终态，失败/degraded/只读 smoke/部分 scope 不替代初始化。较新的失败优先于旧成功；全局在途互斥不变。 |

公开 API：`enqueue_tiktok_bootstrap()` 签名及 HTTP task-result 协议不变，上述复用条件同时适用于手动初始化与 opt-in 增量。empty 仍不能替代真实行为事件生成画像。扩展 `resolveTiktokScopeRoot(doc, scope, expectedViewer?)` 增加可选 viewer，用于本人关注空弹窗边界；`readTiktokUniversalData()` 增加 `explicitLoggedOut`，身份字段本身不代表已登录。

真实安装版的完整结论与失败记录见[验收台账](../platform-source-acceptance.tiktok.md)。本轮未改变跨模块接线、配置字段、CLI 或安装流程。

## 2026-10-12 慢加载与列表交接续修

| 已实现能力 | 限制 |
| --- | --- |
| 慢加载列表识别 | 已证明身份和选中 tab 后延长 scope root 等待；空列表仍需专属显式空态。 |
| 连续 scope 交接 | content 在持久化结果后释放采集锁，不让后台 ACK 等待吞掉下一 scope；不改变串行采集、身份或范围校验。 |
| 原生保存登录判定 | 未出现控件不立即当作登出；共享正向登录证据且明确登出优先，保存和只读确认均使用 20 秒预算。 |

后端公共 API、配置、来源矩阵未改变。实机诊断观察到文档 complete 后登录控件仍需超过 12 秒；当前账号设置页未暴露历史入口，返回 `history_entry_not_found`，不能声明空历史或全能力通过。非空画像、周期增量与站内收藏仍以[真实验收台账](../platform-source-acceptance.tiktok.md)为准。

## 原生收藏授权实测续修（2026-10-12）

已实现：过滤屏幕外预加载视频的收藏控件、选择唯一内层 button、识别当前桌面的金色书签，并强制刷新只读确认 TikTok 的即时成功。公开 API 和账号采集协议未改变；具体语义见 [extension](extension.md#tiktok-原生收藏视口与持久化确认2026-10-12) 与 [saved-sync](saved-sync.md#tiktok-真实写入与刷新确认2026-10-12)。

真实授权写入已经执行，观察到站点发出指定视频的加入收藏请求且返回成功；不过刷新后仍未收藏，收藏列表也返回显式空态，故不能把请求成功当作持久化或画像初始化通过。后续需手动点击对照查清客户端点击/账号或网站持久化边界。历史入口及非空画像/周期增量仍以 [验收台账](../platform-source-acceptance.tiktok.md) 为准，未扩大为完整来源已验收。

账号任务的 `TIKTOK_SCOPE_EXECUTE` 新增接收回执：恢复记录/指针写好后再 ACK，后台有界等待并校验 task/scope，缺失回执重投同一只读 scope。该 ACK 只证明可恢复接收，不能代表列表采集成功或画像完成。


## 原生收藏请求时序（2026-10-13）

已实现：收藏按钮乐观选中后，等待网页原生收藏请求完成才进入刷新确认，避免插件提前导航中断请求。扩展公开环境接口新增 `watchFavoriteRequest(contentId): TiktokFavoriteRequestWatch | null`，详情见 [extension](extension.md#tiktok-收藏请求完成观察2026-10-13)。无 Cookie/响应体读取，无 API 重放；完成记录只用于时序，不作为保存成功证据。

真实请求返回 HTTP 200、业务 0 并完成后，网页刷新仍未保留授权收藏，因此原生写入、非空画像与周期增量仍未全部通过；本次代码回归与已安装版本实测明确区分，见 [验收台账](../platform-source-acceptance.tiktok.md)。


## 可选历史与周期调度接线（2026-10-13）

| 已实现行为 | 边界 |
| --- | --- |
| 默认三项账号信号 | `enqueue_tiktok_bootstrap()` 保持签名不变，手动初始化与周期增量都固定请求 `tt_like / tt_collect / tt_follow`。旧四项任务不会被默认三项任务错误复用；`tt_history` 仍可显式入队，但其可用性不属于当前必过验收范围。 |
| 正式服务调度 | `RuntimeContext` 启动和热重载把 `sources.tiktok.enabled` 传给 `SourceIncrementalSync`。仍要求全局与 TikTok 的 incremental 开关、来源 enabled、画像 ready 和扩展在线；默认不自动开启。 |
| 非空增量与去重 | 两轮非空 fixture 通过正常 API/任务/事件入口：旧收藏不重复、新收藏进入 generic 画像消费路径。该回归不冒充真实账号或模型生成画像。 |

用户明确同意没有网页历史时跳过；实时三项采集已完整得到 0/0/0 的明确空态。空账号不合成画像，站内收藏及非空画像的真实状态见 [最新台账](../platform-source-acceptance.tiktok.md)。
