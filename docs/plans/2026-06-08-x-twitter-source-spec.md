# 2026-06-08 — X (Twitter) 来源接入 Spec

## 0. Scope

给 OpenBiliClaw 增加第六个内容来源 **X (Twitter)**,包含两条独立的数据通路:

1. **行为采集（使用事件获取）** — 在用户自己的 x.com 登录态下,被动偷听用户的互动与点开行为,作为 Soul 画像信号。
2. **发现来源（作为来源进行 discover）** — 三个发现源:**搜索 / 推荐流(For You)/ 账号订阅**,把 X 内容灌入统一候选池参与推荐。

设计原则:**最大化复用现有基建,X ≈「抖音 direct 模式的翻版」** —— 服务端 cookie 重放做发现(对标 `douyin_direct.py`)+ 扩展 MAIN-world tap 做行为采集(对标 `dy-fetch-tap.ts`)+ 自动 cookie 同步(对标 `cookie-sync.ts`)。

**Out of scope（本期不做）:**

- 趋势 / 实时热点源(放 v2;`twitter-cli` 对 trending 支持不确定,且最易跑偏)。
- 后端在 X 上的任何写操作(发帖/点赞/转推等)——后端/`XClient` 全程只读;行为采集只是**被动记录用户自己触发的**互动,不代发。
- 原始首页流逐条曝光采集(只采「显式互动 + 点开」,不采划过的 impression)。
- 多用户 / 多账号支持(沿用项目单用户模型)。
- thread 的深度全量抓取(v1 只取被偷听到 / 被发现命中的 thread 头 + note_tweet 长文文本,不递归补全整条 thread)。

## 1. Background & 关键决策

讨论已确认的选型(连同理由,便于回溯):

| 决策点 | 选定方案 | 理由 |
|--------|----------|------|
| 发现抓取机制 | **服务端 cookie 重放**(用户真实 x.com cookie) | 7×24 常驻(浏览器关着也发现)、免费、不用小号、对 X 有成熟服务端客户端可用。用户已明确接受「主号低频只读自动检索」的风险。 |
| 行为采集机制 | **扩展 MAIN-world GraphQL tap**(被动偷听) | 零额外请求、封号风险最低、就是「用户实际看/做的」、复用 `dy-fetch-tap.ts` / `xhs-token-sniffer.ts` 范式。 |
| cookie 获取 | **扩展自动同步**(已有 `cookie-sync.ts` + `cookies` 权限) | 免去手动 F12 导出;浏览器开着就刷新,cookie 永远新鲜。 |
| 发现源数量 | **3 个:搜索 / 推荐流 / 账号订阅** | 沿「谁决定相关性」轴互补:画像驱动 / X 算法驱动 / 用户精选驱动。 |
| 服务端 X 客户端 | **`twitter-cli` 作 `openbiliclaw[x]` extra**(已确认在 PyPI / Apache-2.0 / 可 import;桌面构建带上,见 §8);`XClient` 优先 **in-process import** 其客户端模块 | `twitter-cli`(Agent-Reach 23.5k★ 同款)已处理 queryId 轮换 + TLS 指纹伪装 + jitter;不必重造。import 比 subprocess 更可靠(冻结桌面包里 CLI 入口未必在 PATH)。in-repo httpx 客户端降级为长期 contingency。 |

**参考实现来源:**
- 服务端读取:`twitter-cli`(github.com/public-clis/twitter-cli)— 逆向 GraphQL + `auth_token`/`ct0` cookie + 动态 queryId fallback + `curl_cffi` TLS 伪装。
- JSON 拆包:`prinsss/twitter-web-exporter` 的 `extractDataFromResponse()`(处理 `TweetWithVisibilityResults` / `TweetTombstone` / `note_tweet` 长文 / 转推引用嵌套)— 直接 port 进后端 normalizer。
- GraphQL operation 名稳定、queryId 每 2~4 周轮换 → **一律按 operation 名匹配,queryId 当通配**。

## 2. 来源身份 & 命名

| 维度 | 取值 | 说明 |
|------|------|------|
| `source_platform` / `source_type` / AdapterRegistry key / 配置段 / pool_source_shares key | `"twitter"` | 内部统一 key。可 grep(单字母 `x` 不行)、与 `SOURCE_*` 常量风格一致、twitter.com 仍解析。 |
| 显示标签 | `"X"` | 经 `event_format._PLATFORM_LABELS["twitter"] = "X"` 渲染。 |
| API 路径前缀 | `/api/sources/x/...` | 沿用 `dy` / `xhs` 的缩写惯例,短。 |
| 扩展产物 | `dist/content/x.js`、`dist/main/x-graphql-tap.js` | |
| cookie 落盘 | `data/x_cookie.json`(对标 `data/douyin_cookie.json`) | 不进 config.toml;可被 `OPENBILICLAW_X_COOKIE` env 覆盖。 |

---

## 3. Part A — 行为采集（使用事件获取）

### 3.1 数据通路

```text
x.com 页面(用户登录态)
  └─ MAIN world: x-graphql-tap.js  ── 包 window.fetch / XHR,按 operation 名匹配
       ├─ 引擎互动 mutation:FavoriteTweet / CreateRetweet / CreateTweet(回复) / CreateBookmark / 关注
       └─ 点开:TweetDetail(打开单条)/ UserByScreenName(访问主页)
  └─ postMessage → isolated content: x.ts (startCollector + tap 监听)
       └─ chrome.runtime.sendMessage BEHAVIOR_EVENT
  └─ service-worker buffer → POST /api/events (复用,零后端改动)
       └─ memory_manager.propagate_event
```

**只采显式互动(强信号),v1 不采主页曝光、不采无 dwell 的点开:**

| X 动作 | GraphQL operation | 映射 event_type | 是否已被评为正信号 |
|--------|-------------------|-----------------|------------------|
| 点赞 | `FavoriteTweet` | `like` | ✅ 已在 `_EXPLICIT_POSITIVE_EVENT_TYPES` |
| 收藏书签 | `CreateBookmark` | `favorite` | ✅ 已在(强:存起来看) |
| 回复 | `CreateTweet`(带 `in_reply_to`) | `comment` | ✅ 已在 |
| 转推 | `CreateRetweet` | `share` | ➖ v1 作 context-tier(不动全局正信号集) |
| 关注 | `FollowUser` / friendships create | `follow` | ➖ v1 作 context-tier(`follow` 其它源已在用) |

> ⚠️ **更正(Codex review M1/R3)**:`classify_event_satisfaction()` 的 `_EXPLICIT_POSITIVE_EVENT_TYPES`(`event_format.py:78`)与 `soul/pipeline.py` 的 `_ENGAGEMENT_TYPES` 都只含 `{like, coin, favorite, comment}`。`share` / `follow` / `view` 都**不在内**,只是渲染标签,不会被评为正信号;`click` 仅在带 dwell 时才评分。
>
> **v1 取舍:只把 like→`like`、收藏→`favorite`、回复→`comment` 当强正信号(三者已在上述两处集合,零全局改动)。** retweet(`share`)/ follow(`follow`)仍**采集为事件**但作 **context-tier**(BEHAVIOR_EVENT),v1 **不**加入全局正信号集 —— 因为 `follow` 已被 Bilibili/抖音/YouTube emit(`cli.py:4472`、`dy_tasks.py:40`、`youtube/takeout.py:333`),全局提升会误改这些源的评分,属本期不该引入的副作用。
>
> **将来若要把 retweet/follow 升为强信号**:须同时改 `_EXPLICIT_POSITIVE_EVENT_TYPES` + `_ENGAGEMENT_TYPES`,并补**跨源回归测试**。
>
> **`view`(点开)/ `click`(访问主页)v1 不纳入评分**:无 dwell 不会干净评分,作上下文。

### 3.2 扩展改动

| 文件 | 新增/改 | 最近似模板 |
|------|---------|-----------|
| `extension/manifest.json` | host_permissions 加 `*://*.x.com/*`、`*://*.twitter.com/*`;content_scripts 加 `x.js`(document_idle)+ `x-graphql-tap.js`(MAIN, document_start) | 现有 xhs 两条 content_script |
| `extension/src/shared/platforms/twitter.ts` | 新增 PlatformAdapter:`sourcePlatform="twitter"`、tweet URL → `content_id`(`/status/(\d+)`)、卡片/搜索框选择器、`inferActionType` | `shared/platforms/xiaohongshu.ts` |
| `extension/src/content/x.ts` | 新增:`startCollector(twitterAdapter)` + 监听 tap 的 postMessage | `content/xiaohongshu.ts` |
| `extension/src/main/x-graphql-tap.ts` | 新增 MAIN-world tap:`classifyXResponseUrl(url)` 按 operation 名匹配、`parse*` 拆包、`installFetchTap`/`installXhrTap`、postMessage 回传 | `main/dy-fetch-tap.ts`(结构 1:1)、`main/xhs-token-sniffer.ts`(JSON 深度遍历容错) |

### 3.3 后端

`event_format.py`:加 `SOURCE_TWITTER = "twitter"` 与 `_PLATFORM_LABELS["twitter"] = "X"`。其余通路(`/api/events` → `build_event` → `propagate_event`)**零改动**。

---

## 4. Part B — 发现来源（3 个源,服务端 cookie 重放）

### 4.1 数据通路

```text
扩展 cookie-sync.ts（加 x.com 分支）
  └─ POST /api/sources/x/cookie → 落盘 data/x_cookie.json（auth_token + ct0）
后端 x_producer.py（refresh 循环调度,对标 douyin/youtube producer）
  └─ 按 Soul 画像 + 预算 + 节流，决定本轮跑哪些 recipe
       ├─ search   → XSearchStrategy   → XClient.search(kw)
       ├─ feed     → XForYouStrategy   → XClient.for_you()
       └─ creator  → XCreatorStrategy  → XClient.user_tweets(handle)
  └─ XClient（in-process import twitter-cli，cookie 经 env 注入；详见 §4.3）
  └─ 每条 tweet → normalize → DiscoveredContent(source_platform="twitter")
  └─ 进统一候选池 pending-evaluation（与其它源同一评估器）
```

与 XHS 的关键区别:**X 的 `XAdapter.fetch()` 是真实实现(服务端可跑),不是 stub**。这点更像 Bilibili / Douyin-direct,而非 XHS 的「扩展灌入 + stub adapter」。

### 4.2 cookie 桥

| 文件 | 改动 | 模板 |
|------|------|------|
| `extension/src/background/cookie-sync.ts` | 加 `readXCookieHeader()`(要求 `auth_token` + `ct0`)、`syncXCookieToBackend()`、onChanged/alarm/startup 里挂 x.com 分支 | 现成 `readDouyinCookieHeader` / `syncDouyinCookieToBackend` |
| `api/app.py` | 加 `POST /api/sources/x/cookie`(存 `data/x_cookie.json`,可选 smoke 验证) | `POST /api/sources/dy/cookie` |
| `api/models.py` | `XCookieIn` / `XCookieResponse` | `DouyinCookieIn` / `DouyinCookieResponse` |

必需 cookie:`auth_token`(会话)+ `ct0`(CSRF,回填 `x-csrf-token`)。

### 4.3 服务端 X 客户端

**`sources/x_client.py`（新增）** —— 在 `XClient` 后面封装抓取实现,对外暴露:

```python
class XClient:
    def __init__(self, cookie: str) -> None: ...      # 解析 auth_token / ct0
    async def search(self, query: str, *, limit: int, product: str = "Top") -> list[dict]: ...
    async def for_you(self, *, limit: int) -> list[dict]: ...
    async def user_tweets(self, handle: str, *, limit: int) -> list[dict]: ...
```

- **依赖(已查清 2026-06-08)**:`twitter-cli` **在 PyPI 上**(v0.8.x,Apache-2.0,requires-python ≥3.10,依赖 `curl_cffi>=0.7` + `xclienttransaction` + bs4),`twitter_cli` 是**可 import 的包**。→ 直接 pin 版本(`twitter-cli>=0.8.5`),**无需 git 引用,不阻断 openbiliclaw 自身 PyPI 发布**;采用可选 extra `openbiliclaw[x]`、桌面构建带上(见 §8)。⚠️ PyPI 包地址指向 `jackwener/twitter-cli`,Agent-Reach 用 `public-clis/twitter-cli`(同包名/依赖,疑同源)—— B.0 确认 canonical 来源再 pin。
- **lazy import(无论核心/extra 都必须做)**:`twitter-cli` / `curl_cffi` 只在 X 启用且真正 fetch 时才 import;`enabled=false` 路径**绝不 import**,确保未启用 X 的用户即使该依赖缺失/损坏也不影响后端启动与其它源。
- **集成模式**:`XClient` **优先 in-process import** `twitter-cli` 的客户端模块直接调用 —— 比 subprocess 更可靠(冻结桌面包里 CLI 入口未必在 PATH,且省去 JSON round-trip)。cookie 经 `TWITTER_AUTH_TOKEN` / `TWITTER_CT0` env 或直接传参注入。仅当其内部不可 import 时才退回 subprocess 调 CLI(`--json` 输出)。
- **contingency**:若 `twitter-cli` 长期失修,改 in-repo `httpx` GraphQL 客户端(对标 `douyin_direct.py` 的 `DouyinDirectClient`,把 XBogus 签名换成「带 cookie + 硬编码 web Bearer + 按 operation 名命中、queryId 动态发现」)。边界稳定,可热替换。

> **B.0 spike(实现第一步,约半天)**:① ✅ **已确认在 PyPI**(`twitter-cli` v0.8.x,Apache-2.0)—— 可直接 pin,无 git 引用问题;② 确认 canonical 来源(`jackwener` vs `public-clis`)+ `twitter_cli` 客户端 API 可 in-process import(不止 CLI);③ 用一份 `auth_token`+`ct0` 验证 search / for-you / user-timeline 跑通且输出可解析;④ 定 pin 版本 + 集成模式 + PyInstaller 的 `curl_cffi` hook。

### 4.4 三个发现策略 & adapter

| 文件 | 内容 | 模板 |
|------|------|------|
| `sources/twitter_adapter.py` | `XAdapter`:`source_type="twitter"`;`fetch(recipe, profile, limit)` 按 `recipe.strategy` 分派到三个策略 | `sources/bilibili_adapter.py`(委派式 adapter) |
| `discovery/strategies/x.py` | `XSearchStrategy`(画像生成关键词,复用 `xhs_keyword_gen` 思路)、`XForYouStrategy`、`XCreatorStrategy` | `strategies/youtube.py`、`strategies/search.py` |
| `discovery/x_normalize.py` | tweet JSON → `DiscoveredContent`(见 §5.2);port `extractDataFromResponse` 拆包逻辑 | `douyin_direct.normalize_aweme_item` |
| `runtime/x_producer.py` | refresh tick 调度:按 `[sources.twitter]` 预算/节流决定跑哪些 recipe;关注 `min_interval_minutes` | `runtime/xhs_producer.py`、youtube producer |
| `api/runtime_context.py` | 注册 `XAdapter`;装配三策略;接 producer | 现有 bilibili/xhs 注册块 |

### 4.5 账号订阅(creator subscription)

因发现走服务端,**账号订阅比 XHS 更简单**:不需要扩展后台标签页往返,producer 直接服务端拉。

| 文件 | 改动 | 模板 |
|------|------|------|
| `storage/database.py` 或 `sources/x_tasks.py` | `x_creator_subscriptions` 表 + CRUD(`handle`、`added_at`、`last_fetched_at`) | `xhs_creator_subscriptions`(`xhs_tasks.py:462`) |
| `api/app.py` | `GET/POST/DELETE /api/sources/x/creators` | XHS `/api/sources/xhs/creators` |
| `runtime/x_producer.py` | 每个订阅按 `account_sync_interval` 入队一次 `creator` recipe | XHS 创作者 nightly 调度 |

---

## 5. 数据模型改动（唯一跨切面的真实改动:文字形态）

X 是首个**以文字为主**的来源,现有模型为视频/图文设计(`cover_url`/`duration`/`view_count`)。决策:**全形态,给模型加「文字/thread」内容类型**。

### 5.1 `DiscoveredContent`(`discovery/engine.py`)新增字段

```python
body_text: str = ""          # 推文/thread 全文(纯文字内容的主体;视频源留空)
content_type: str = "video"  # 复用候选池既有 shape 字段:"video"|"note"|"tweet"|"thread"
```

> ⚠️ **更正(Codex review M2)**:不新造 `media_type`。候选池**已用 `content_type`** 表达内容形态 —— `DiscoveryCandidateWrite.content_type`(`candidate_pool.py:42`)、`discovery_candidates.content_type`(`database.py`)。新造平行字段会让 X 文字/thread 候选无法正确流过 pending 评估。改为给 `DiscoveredContent` 加 **`content_type`**(默认 `"video"`,X 填 `"tweet"` / `"thread"`)。

- `__post_init__`:X 不需要 bilibili 那套 fallback(adapter 显式填 `source_platform`/`content_id`/`content_url`/`author_name`/`content_type`)。
- `to_cache_kwargs()`:加 `body_text` + `content_type` 透传。
- **两处**硬编码 `content_type="note" if xiaohongshu else "video"` 都要改成优先取 `item.content_type`(`item.content_type or ("note" if xhs else "video")`),否则 X 候选被强标成 `video`:`discovered_content_to_candidate_write()`(`candidate_pool.py:137`)**和** discovery 引擎候选 dict 构建(`engine.py:1274`,Codex R3 发现的第二处)。

### 5.2 tweet → DiscoveredContent 字段映射

| DiscoveredContent | tweet 来源 |
|-------------------|-----------|
| `content_id` | tweet rest_id |
| `content_url` | `https://x.com/<handle>/status/<id>` |
| `source_platform` | `"twitter"` |
| `author_name` | `@<screen_name>` |
| `title` | 推文首行 / 截断文本(供卡片标题与列表) |
| `body_text` | `note_tweet` 长文 或 `full_text`(完整正文) |
| `content_type` | 多条连推→`thread`,否则→`tweet`(有无图/视频记进 metadata,不进 content_type) |
| `cover_url` | 媒体缩略图;纯文字留空 |
| `view_count` / `like_count` | `legacy.{view_count,favorite_count}` |
| `tags` | 标签/话题(hashtags) |

### 5.3 下游适配

| 处 | 改动 |
|----|------|
| `storage/database.py` | **真实迁移 helper(Codex R3 更正:原引的 `_ensure_content_cache_columns()` 不存在)**:`content_cache` 经 `_ensure_content_cache_multisource_columns()`(`database.py:327/3600`,现仅加 content_id/url/source_platform/author_name)补 `body_text` + `content_type`;`discovery_candidates` 已有 `content_type`,经 `_ensure_discovery_candidate_columns()`(`database.py:331/3621`)补 `body_text`。**走 `_ensure_*` 机制,不手写 ALTER**;新建库 fresh schema 同步加列。**全链路透传(Codex M3)**:`cache_content` + enqueue → claim → admission → cache writer → API 序列化,任一处漏掉都会被静默丢(§9 round-trip 测试兜住) |
| `llm/prompts.py` | 推荐解释 / 评估 builder 的 **user_prompt** 里带上 `body_text`(纯文字推文标题信息量低)。**严守 prompt-cache 约定**:system 保持静态,per-call 变量只进 user message,`json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)`。新 builder 必须纳入 `test_prompt_builder_system_messages_are_call_invariant`(见 §9) |
| 推荐卡 UI(`web/` popup/side panel + `extension` popup) | 支持「无封面文字卡」:`content_type ∈ {tweet,thread}`(或 `cover_url` 空)时渲染文字主体而非封面图 |
| `recommendation/engine.py` | franchise/diversity 等逻辑确认对文字内容不炸(`cover_url` 空、`duration` 0) |

---

## 6. 配置

`config.example.toml` 加段(对标 `[sources.douyin]`):

```toml
[sources.twitter]
# 设为 false 后：后端不再生成 X 搜索/推荐流/账号订阅任务，
# pool_source_shares 中的 twitter 配额会被丢弃。默认 false，
# 由 init 交互/--yes-x/插件设置页打开后才写回 true。
enabled = false
mode = "cookie"                         # 服务端 cookie 重放
cookie_env = "OPENBILICLAW_X_COOKIE"    # 优先级高于 data/x_cookie.json
daily_search_budget = 0                 # 0 = 不设每日上限
daily_feed_budget = 0                   # 推荐流(For You)每日拉取上限
daily_creator_budget = 0                # 账号订阅每日抓取上限
request_interval_seconds = 3            # 两次请求最小间隔(抗检测)
min_interval_minutes = 60               # producer 两次执行最小间隔
```

```toml
[scheduler.pool_source_shares]
# ... 现有 bilibili/xiaohongshu/douyin/youtube ...
twitter = 1
```

`config.py`(Codex review M6 —— 比原先说的更硬编码,按符号逐处改,不写易漂移的行号):
- `SourcesConfig` 加 `twitter: SourcesTwitter`
- 默认 `pool_source_shares` 字面量加 `twitter`
- TOML 解析与**序列化 / `config-show` 写出**两处都枚举了 4 个源 —— 不补 twitter 会在生成的 config 里丢掉该源/配额
- env override(对标 douyin)

**扩展设置 UI 同样硬编码 4 个源**(`popup.js` 的 `cfgPoolShare*` / `enabled_sources` / `INIT_SOURCE_OPTIONS`、`popup.html` 的 `data-source-card`):加 X 源卡 + 开关 + 配额输入,并补 round-trip 测试(见 §9)。

---

## 7. 抗检测 & 安全(用户已接受主号低频只读风险)

- **后端只读**:`XClient` 不暴露任何写操作;行为采集只被动记录用户自己触发的互动,后端不代发(措辞更正,Codex MINOR)。
- **节奏**:`request_interval_seconds` + producer `min_interval_minutes`;**推荐流(For You)拉取频率压到每天数次 + jitter** —— 高频拉 home timeline 是最易被注意的行为。
- **TLS / 指纹**:由 `twitter-cli`(`curl_cffi`)负责;若走 in-repo fallback 需补 UA + 合理 header。
- **cookie 新鲜度**:扩展 onChanged/hourly 自动刷;cookie 失效时后端记录、退避,等下次 x.com 登录恢复(对标 bilibili cookie 的失效退避策略)。
- **源健康状态机(Codex MINOR)**:持久化 X 源健康 —— `ok` / `missing_cookie` / `expired_cookie`(401)/ `rate_limited`(429,带冷却)/ `blocked`(403);区分 401/403/429 分别退避;连续失败自动暂停 For-You 拉取;状态经 API 暴露到设置页。
- **隐私**:cookie 仅落本地 `data/x_cookie.json`,不外传;沿用项目本地优先原则。
- **预算护栏**:`daily_*_budget` 兜底,防止异常循环打爆请求量。

---

## 8. 仍需拍板 / 实现期确认

1. **`twitter-cli` 打包形态 — 已基本收敛(2026-06-08 查清)**:
   - **PyPI 可得性:✅ 已确认**。`twitter-cli` 在 PyPI(v0.8.5,2026-03-17,Apache-2.0,requires-python ≥3.10)。→ 可直接 pin 版本,**git 引用阻断 openbiliclaw 自身 PyPI 发布的风险消失**。
   - **核心依赖 vs 可选 extra:采用 extra**。PyPI 阻断没了之后,「核心 vs extra」只剩「要不要塞给所有用户」一维 —— 可选 `openbiliclaw[x] = ["twitter-cli>=0.8.5"]` 严格占优(非 X 用户零负担)+ §4.3 lazy import。你原先「打到包里」由「桌面构建带上 extra」满足。
   - **桌面构建子决策(仅剩这一个真要你定)**:桌面包 (a) **始终带 extra**(= 你原意「打到包里」,零配置,但非 X 桌面用户也收到 scraper+`curl_cffi` 二进制)/ (b) **按需下载**(启用 X 时再拉)。**我建议 (a)**,贴合你原意且零配置;未定前 spec 记为 (a)。
   - **`curl_cffi` 原生二进制** — PyInstaller 需补 hook 收各 OS·arch 二进制;桌面包体积增大。
   - **canonical 来源 + 维护** — PyPI 包地址 `jackwener/twitter-cli` vs Agent-Reach 的 `public-clis/twitter-cli`(同包名/依赖,疑同源/镜像);B.0 确认可信来源再 pin,X 改版时 bump。
2. **推荐流去重** — 候选池内按 `twitter:<tweet_id>` 对**发现到的 X 候选**去重;注意这**不**会与行为采集偷听到的推文去重(行为事件不是候选行,是独立的 Soul 信号),两者本就不同表(Codex NIT 更正)。
3. **thread 聚合深度** — v1 只取 thread 头 + `note_tweet` 长文;是否需要顺 `conversation_id` 补全整条留待 v2。
4. **行为采集是否纳入主页曝光** — v1 不采;若画像样本不足再考虑「停留 N 秒的曝光」。
5. **init / 设置页开关** — X 源的开启入口(交互式 init 提示 + 插件设置页 toggle + `--yes-x`),对标 XHS 开启流程。

### B.0 spike 结果(2026-06-08,已执行 — 免 cookie 部分)

- **安装**:`twitter-cli 0.8.5` 从 PyPI 装入 `.venv`(Python 3.14)干净通过;`curl_cffi 0.15.0` 用 `cp310-abi3` wheel(跨 3.10+ 通用 → PyInstaller 利好)。
- **集成模式:in-process import 确认可行**(非 CLI-only)。`twitter_cli.client.TwitterClient(auth_token, ct0, rate_limit_config=None, cookie_string=None)` —— 构造直接吃 `auth_token`+`ct0`。
- **读接口 → XClient 三策略直接映射**:搜索 `fetch_search(query, count=20, product="Top")`;推荐流(For You)`fetch_home_timeline(count=20)`(关注流另有 `fetch_following_feed`);账号 `fetch_user_tweets(user_id, count=20)`(handle→id 用 `resolve_user_id()` / `fetch_user(screen_name)`);另有 `fetch_tweet_detail` 等。
- **方法是同步的** → XClient 用 `asyncio.to_thread(...)` 包成 async。
- **返回已解析的 `twitter_cli.models.Tweet`(非裸 JSON)** → **Task 7 normalize 直接从 `Tweet`(或 `serialization.tweet_to_dict`)映射到 `DiscoveredContent`,不必 port GraphQL 拆包**(库已做)。
- **异常**:`client.TwitterAPIError(status_code: int, message: str)`(带 `status_code`/`error_code`)+ `exceptions.AuthenticationError`,均继承 `TwitterError(RuntimeError)` → 健康状态直接读 `status_code`(401/403/429)。
- **canonical**:PyPI 包名 `twitter-cli`(repo `jackwener/twitter-cli`),pin `>=0.8.5`。
- **仍需 cookie(待用户给 x.com `auth_token`+`ct0`)**:真实数据 smoke + 行为采集 mutation fixture 实采。单元测试一律 mock + 合成 fixture,不阻塞自动化验收。

---

## 9. 测试计划(CLAUDE.md 强制补测)

CLAUDE.md 要求新增功能默认补单元测试。本特性横跨 扩展 tap / 事件 taxonomy / DB schema / prompt builder / 候选池 admission / config / API / 打包 / UI,且上面 Codex review 已暴露多处「以为现成其实不然」,测试不可省。**每个 Phase 落地时同时补对应测试**:

| 测试 | 覆盖 | 位置 |
|------|------|------|
| 事件评分 | `classify_event_satisfaction` 对 X 映射:like→like / 收藏→favorite / 回复→comment 评为正;retweet/follow/view/click **不**被误标为强正信号 | `tests/test_event_format*.py` |
| 候选 round-trip | `DiscoveredContent(content_type="thread", body_text=…)` → enqueue → claim → admission → `content_cache`,`body_text`/`content_type` 全程不丢 | `tests/`(候选池 / database) |
| normalize | tweet JSON(`note_tweet` 长文 / thread / 转推引用 / tombstone)→ `DiscoveredContent` 字段正确 | `tests/test_x_normalize.py` |
| prompt-cache 不变量 | 新 builder 纳入 `test_prompt_builder_system_messages_are_call_invariant`;断言 `body_text` 进 user message、system 字节不变 | `tests/test_llm_prompts.py` |
| 关闭即 no-op | `enabled=false` 时不 import twitter-cli、不下发任务、配额被剔除 | `tests/`(source_policy / runtime) |
| config round-trip | `[sources.twitter]` + `pool_source_shares.twitter` 经 load→save→`config-show` 不丢 | `tests/test_config*.py`、`tests/test_cli.py` |
| API 端点 | `/api/sources/x/cookie`、`/api/sources/x/creators` CRUD | `tests/test_api*.py` |
| 扩展 | `twitter.ts`(URL→id、inferAction)、`x-graphql-tap` 的 `classifyXResponseUrl` / parse | `extension/tests/*.test.ts` |

## 10. 分阶段实现计划

| Phase | 交付 | 依赖 |
|-------|------|------|
| **B.0 Spike** | 查 `twitter-cli` PyPI 可得性 + 可 import 性;验证仅凭 `auth_token`+`ct0` 跑通 search/for-you/user-timeline 且输出可解析;定 pin 版本 + 集成模式 + `pyproject` 依赖写法 | 一份能登录的 x.com cookie |
| **A. 行为采集** | manifest + `twitter.ts` adapter + `x.ts` + `x-graphql-tap.ts` + `event_format` 常量;端到端「在 X 点赞/收藏/点开 → /api/events → 画像」 | 无(后端通路现成) |
| **P1. cookie 桥** | `cookie-sync.ts` x.com 分支 + `/api/sources/x/cookie` + 落盘 | A 的 manifest 权限 |
| **P2. 数据模型** | `DiscoveredContent.body_text/content_type` + `_ensure_*_columns` 迁移 + 全链路透传(enqueue/admission/cache/API)+ `to_cache_kwargs` + 文字卡 UI + prompts 带 body_text + §9 round-trip 测试 | 无 |
| **P3. 发现核心** | `XClient` + `x_normalize` + `XAdapter` + 三策略 + runtime 注册;手动触发能把 X 内容灌入候选池并出现在推荐 | B.0, P1, P2 |
| **P4. 账号订阅** | `x_creator_subscriptions` 表 + `/api/sources/x/creators` + producer 调度 | P3 |
| **P5. 调度 & 配置** | `x_producer.py` + `[sources.twitter]` 配置 + `pool_source_shares` + 开启入口 | P3 |
| **P6. 文档同步** | 见 §11 | 全部 |

## 11. 文档同步清单(CLAUDE.md 硬性要求)

- [ ] `docs/modules/extension.md` — 新增 X content script / tap / cookie 分支
- [ ] `docs/modules/<discovery/sources/config/cli>.md` — 新增 X adapter/策略/client、`[sources.twitter]` 配置、相关 CLI(若有)
- [ ] `docs/changelog.md` — 顶部新版本条目 + 本 PR 短 bullet
- [ ] **架构图**(动了跨模块接线 + 新增依赖 twitter-cli):`docs/architecture.md` + `docs/spec.md` §3 + `README.md` / `README_EN.md` 顶部架构图,全部加上 X 源
- [ ] `README.md` / `README_EN.md` 📌 highlights callout(若发版):X 多平台扩展,≤4 bullets,CN/EN 同步
- [ ] `config.example.toml` 注释 + `docs/modules/config.md`
- [ ] **依赖/打包变更**(新增可选 extra `openbiliclaw[x]` = `twitter-cli>=0.8.5` + 桌面构建带上该 extra + `curl_cffi` 原生二进制 PyInstaller hook):`docs/docker-deployment.md` / `docs/agent-install.md` / `scripts/install.sh` 依赖说明同步
- [ ] GitHub About(若定位措辞变化)

## 12. 新增/修改文件清单（→ 最近似模板）

**新增**
- `extension/src/shared/platforms/twitter.ts` → `…/xiaohongshu.ts`
- `extension/src/content/x.ts` → `content/xiaohongshu.ts`
- `extension/src/main/x-graphql-tap.ts` → `main/dy-fetch-tap.ts`
- `src/openbiliclaw/sources/twitter_adapter.py` → `sources/bilibili_adapter.py`
- `src/openbiliclaw/sources/x_client.py` → `sources/douyin_direct.py`
- `src/openbiliclaw/sources/x_tasks.py`(账号订阅 CRUD)→ `sources/xhs_tasks.py`
- `src/openbiliclaw/discovery/strategies/x.py` → `strategies/youtube.py`
- `src/openbiliclaw/discovery/x_normalize.py` → `douyin_direct.normalize_aweme_item`
- `src/openbiliclaw/runtime/x_producer.py` → `runtime/xhs_producer.py`

**修改**
- `extension/manifest.json`、`extension/src/background/cookie-sync.ts`
- `extension/popup/popup.js`、`popup.html`、`popup-init-control.js`(设置页加 X 源卡/开关/配额、init 源选项 —— Codex M6)
- `src/openbiliclaw/sources/event_format.py`(`SOURCE_TWITTER` + `_PLATFORM_LABELS["twitter"]="X"`;**v1 不动全局正信号集**,retweet/follow 走 context-tier —— Codex M1/R3)
- `api/app.py`、`api/models.py`、`api/runtime_context.py`
- `src/openbiliclaw/discovery/engine.py`(`DiscoveredContent` 加 body_text/content_type;改 `engine.py:1274` **第二处** content_type 硬编码 —— Codex R3)、`discovery/candidate_pool.py`(content_type 透传 + 改 `:137` 硬编码 —— Codex M2)
- `storage/database.py`(`_ensure_*_columns` 加 body_text + 全链路透传 + 订阅表 —— Codex M3)
- `src/openbiliclaw/llm/prompts.py`、`recommendation/engine.py`、推荐卡前端
- `config.example.toml`、`src/openbiliclaw/config.py`(SourcesConfig + 默认 shares + 解析 + 序列化/config-show —— Codex M6)
- `pyproject.toml`(`[project.optional-dependencies] x = ["twitter-cli>=0.8.5"]`,见 §8)、`packaging/build.py` 桌面构建带上 `[x]` extra + PyInstaller hooks(收集 `curl_cffi` 原生二进制)

**新增测试(见 §9)**
- `tests/test_x_normalize.py`、`extension/tests/*.test.ts`(twitter adapter / tap)
- 扩充:`tests/test_event_format*.py`、`tests/test_llm_prompts.py`、`tests/test_config*.py`、`tests/test_cli.py`、`tests/test_api*.py`、候选池/database round-trip
