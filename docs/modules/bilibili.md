# B 站接入层

> API 优先的 B 站数据访问层，包含 Cookie 认证、核心 API 封装和 agent-browser 浏览器集成。

## 概述

`bilibili/` 包是系统访问 B 站 API 的唯一出口；B 站 steady-state discovery 还会通过 `sources/bili_tasks.py`、`runtime/bilibili_producer.py` 和浏览器扩展提供搜索兜底任务桥。整体分四层：

1. **AuthManager** — Cookie 管理和登录验证
2. **BilibiliAPIClient** — HTTP API 封装（主访问路径）
3. **BilibiliBrowser** — agent-browser CLI 封装（API 无法覆盖的操作）
4. **Bili extension search fallback** — API 搜索进入冷却或暴露短期 DOM fallback 降级信号且扩展在线时，后端可入队搜索任务；扩展打开真实搜索页、抓渲染后的结果并回传统一候选池

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 3.1 Cookie 认证 | ✅ | set / load / validate / clear + CLI auth 命令 + 运行时 cookie 回退 |
| 扩展 Cookie 自动同步 | ✅ | 浏览器扩展可 POST `/api/bilibili/cookie` 持久化 Cookie；后端在 background runtime-stream 连接且缺 Cookie 时会发 `bilibili_cookie_sync_requested` 主动要求扩展回传 |
| 3.2 核心 API | ✅ | 10+ API 方法 + 限流 + 统一错误处理 |
| 登录态诊断 | ✅ | 任意 `_get_json()` endpoint 返回 `-101` 时抛 `BilibiliAuthExpiredError`；日志使用固定 session-expired 文案提示重新登录或保持扩展在线，不复制远端 message/body。 |
| 搜索 WBI 化与 412 软降级 | ✅ | `search()` 现会先从 `nav` 获取 WBI key，走 `/x/web-interface/wbi/search/type`；遇到 `412 Precondition Failed` 时会记录 warning 并返回空结果，避免拖垮整轮 discover |
| 搜索风控冷却（分级） | ✅ | 412（显式 IP 封禁）即时进入硬冷却（base 600s）；`v_voucher`（多为 WBI key churn / 轻限流）改为**阈值化软冷却**——单个关键词耗尽重试只记一次 streak、不触发冷却（整轮其余关键词 + 共用此冷却的 explore 继续出货），但会打开短期 `search_dom_fallback_remaining()` 信号让扩展可补一轮真实搜索页；连续 `_SEARCH_VOUCHER_BLOCK_THRESHOLD`（默认 3）个关键词级耗尽才启用进程级 cooldown（base 缩到 180s）；一旦怀疑风暴（streak>0）后续关键词只做单次快探测、不再每词 ~21s 硬抗，任一成功即清零 streak。所有 BilibiliAPIClient 实例共享冷却和 DOM fallback 状态 |
| 扩展搜索兜底任务桥 | ✅ | `BilibiliExtensionSearchProducer` 在 `search_cooldown_remaining()>0` 或 `search_dom_fallback_remaining()>0`、扩展 presence 在线、B 站池子低于 quota 时入队 `bili_tasks(type="search")`；扩展后台打开 `search.bilibili.com/all?keyword=...`，content script 抓渲染后的搜索卡片并 POST `/api/sources/bili/task-result`，后端写入 `discovery_candidates`，后续仍由统一 evaluator 判断是否入池 |
| 近期供给 lane | ✅ | API 与扩展搜索都在既有请求/关键词预算内把每轮第 1 个 query 标为 `order="pubdate"`，最多取 5 条；它仍使用 `source_strategy="search"` / `bili-extension-search` 与同一 admission 规则，只用 `discovery_lane="recent"`、`source_context` 和 raw payload 保留可回放的检索来源。 |
| 弹幕 fetch outcome | ✅ | `get_danmaku_texts_result()` 区分 `success`（可为空）、`no_data` 与 `transient_failure`；HTTP、XML、限速和网络异常保留原因并交给上层重试。HTTP 200 但根元素不是 B 站 `<i>`（包括 HTML challenge）不会伪装成成功空结果。`get_danmaku_texts()` 继续提供只返回文本列表的兼容包装 |
| 账户侧同步来源 | ✅ | 已支持 history / favorites / following 三类长期信号，供后台低频同步使用；favorites 会按收藏夹分页补齐到预算上限；同步事件会带 `metadata.signal_strength` 供偏好分析区分证据强弱 |
| 原生收藏 / 稍后再看写入 | ✅ | `BilibiliAPIClient` 新增认证 form POST、exact-title 收藏夹复用/创建、视频收藏和稍后再看写入；`BilibiliNativeSaveAdapter` 将 B 站 application code 归一化为 saved-sync 状态，并已由 `RuntimeContext` 注册到平台中立 `/api/saved/*`。UI 仍属后续任务；默认关闭自动同步，旧 B 站保存端点仍只写本地。 |
| 3.3 agent-browser 集成 | ✅ | navigate / get_page_content + CLI browser 命令 |

### Danmaku outcome API

`BilibiliAPIClient.get_danmaku_texts_result(cid, limit=...)` 返回
`DanmakuFetchResult(status, texts, reason)`；`status` 为 `success`、`no_data` 或
`transient_failure`。HTTP 200 的非 `<i>` 根元素也属于 `transient_failure`。持久化调用方必须使用该结果保留成功空结果与可重试失败的区别；旧
`get_danmaku_texts()` 仅作为返回文本列表的兼容包装。

## 公开 API

### AuthManager

```python
from openbiliclaw.bilibili import AuthManager

manager = AuthManager(data_dir=Path("data"))
manager.set_cookie("SESSDATA=abc; bili_jct=xyz")  # 持久化到 data/bilibili_cookie.json
manager.load_cookie()                              # 从磁盘恢复

status = await manager.validate_cookie("SESSDATA=abc")
# AuthStatus(has_cookie=True, authenticated=True, username="alice", user_id=10086)

status = await manager.get_status()  # 加载本地 cookie 并验证
manager.clear_cookie()               # 删除 cookie 文件
```

### 运行时 Cookie 解析

```python
from openbiliclaw.bilibili.auth import resolve_runtime_cookie

cookie = resolve_runtime_cookie(
    data_dir=Path("data"),
    configured_cookie="",
)
# 优先使用 config.toml 中的显式 cookie；
# 若为空，则自动回退到 auth login 保存的 data/bilibili_cookie.json
```

### BilibiliAPIClient

```python
from openbiliclaw.bilibili import BilibiliAPIClient, BilibiliAuthExpiredError

# v0.3.153+：客户端 trust_env=False——恒直连，不继承环境变量 / 系统代理
# （代理出口 IP 常触发 B站 风控，已登录也会显示"未登录"）。
# 网络必须走代理时用 proxy= 显式指定（对应 config.toml [bilibili].proxy）。
client = BilibiliAPIClient(cookie="SESSDATA=abc", min_request_interval=0.2, proxy=None)

# 认证
try:
    nav = await client.get_nav_info()  # NavInfo(is_login=True, uname="alice", mid=10086)
except BilibiliAuthExpiredError:
    # Cookie 已过期或未登录；重新登录 B 站，或保持浏览器扩展在线自动同步新 Cookie
    raise

# 观看历史（cursor-based 分页）
history = await client.get_user_history(max_items=200)
all_history = await client.get_user_history(max_items=0)  # 拉取全部可用历史

# 搜索
results = await client.search("纪录片", page=1, order="pubdate")
# search 请求会使用 WBI 签名 + 搜索页 Referer；
# 若 B 站返回 412 或连续 v_voucher，则这里会保守返回 []，
# 并让进程内后续 search 短期冷却。
remaining = client.search_cooldown_remaining()

# 收藏
folders = await client.get_favorite_folders()  # list[FavoriteFolder]
folder_items = await client.get_favorites(media_id=folders[0].media_id, max_items=100)
all_fav = await client.get_all_favorites(
    max_folders=10,
    max_items_per_folder=50,
    max_total_items=300,
)
# max_total_items 是跨收藏夹总预算；不传时仅按每个收藏夹的 max_items_per_folder 控制。

# 原生保存（会修改 B 站账号；仅在用户明确触发同步后调用）
folder = await client.ensure_favorite_folder("OpenBiliClaw")
await client.add_video_to_favorite("BV1xx411c7mD", folder.media_id)
await client.add_video_to_watch_later("BV1xx411c7mD")

# 关注
following = await client.get_following(page=1, page_size=50)  # list[FollowingUser]

# 视频
video = await client.get_video_info("BV1xx411c7mD")  # VideoInfo
related = await client.get_related_videos("BV1xx411c7mD")

# 评论
comments = await client.get_video_comments("BV1xx411c7mD", limit=20)  # list[CommentInfo]

# 排行榜
ranking = await client.get_ranking(rid=0)

await client.close()
```

### BilibiliBrowser

```python
from openbiliclaw.bilibili.browser import BilibiliBrowser

browser = BilibiliBrowser(executable="agent-browser", headed=False)
print(browser.is_available)  # True / False

result = await browser.navigate("https://www.bilibili.com/video/BV1xx411c7mD")
content = await browser.get_page_content("https://www.bilibili.com/video/BV1xx411c7mD")
```

### BiliTaskQueue

```python
from openbiliclaw.sources.bili_tasks import BiliTaskQueue

queue = BiliTaskQueue(database)
task_id = queue.enqueue_with_id(
    "search",
    {
        "query": "机械键盘 声音",
        "limit": 5,
        "order": "pubdate",
        "discovery_lane": "recent",
        "source": "bili-extension-search",
    },
)
task = queue.next_pending()
queue.merge_result(task_id, videos=[{"bvid": "BV...", "title": "..."}], complete=True)
```

任务端点：

- `GET /api/sources/bili/next-task`：扩展领取最旧 pending B 站任务，领取后标记 `in_progress`。
- `POST /api/sources/bili/task-result`：接收 `ok` / `partial` / `empty` / `failed` 结果；视频结果转换为 `DiscoveredContent` 后写入 `discovery_candidates`。任务 payload 带 `discovery_lane="recent"` 时，候选保存 `source_context="bili-extension-search:recent"` 与同名 raw provenance，但不改变策略、评分或 admission 阈值。
- `POST /api/sources/bili/kick`：通过 runtime stream 广播 `bili_task_available`，让扩展可立即 poll。

扩展侧协议：

- `background/bili-task-dispatcher.ts`：轮询 `/api/sources/bili/next-task`，收到 search task 后打开后台搜索页；近期任务会把 `order=pubdate` 写入真实 B 站 URL。tab ready 后向 B 站 content script 发送 `BILI_TASK_EXECUTE`；如果 Chrome 报 content script listener 暂未就绪，会在 8 秒窗口内短重试，避免真实页面 complete 早于 isolated content script 注册时把任务误标失败。
- `content/bili/task-executor.ts`：不直连 B 站 API，不生成 WBI 签名，只读取真实页面已渲染的 `.bili-video-card` / `.video-list-item` 等结果卡片，提取 `bvid/title/up_name/url/cover_url/view_count/duration/description`。
- `service-worker.ts`：监听 runtime stream 的 `bili_task_available` 事件做即时 poll，接收 `BILI_TASK_RESULT` 并回传后端；普通 alarm 作为兜底。

真实端到端验证：

```bash
BILI_EXTENSION_E2E=1 .venv/bin/pytest tests/test_bili_extension_browser_e2e.py -q -s
```

该 harness 使用临时后端和临时 SQLite，不污染生产数据；它等待真实扩展 runtime-stream presence，强制进程内 B 站 search cooldown，再由真实 `BilibiliExtensionSearchProducer` 入队，最后要求扩展打开真实 `search.bilibili.com` 页面并完成 DOM 结果回传。

### 数据结构

| 类 | 用途 |
|----|------|
| `NavInfo` | 登录用户基本信息（is_login, uname, mid） |
| `VideoInfo` | 视频详情（标题、UP主、播放/点赞/收藏数等） |
| `FavoriteFolder` | 收藏夹元数据（media_id, title, media_count） |
| `FavoriteFolderWithItems` | 收藏夹 + 内容列表 + truncated 标记 |
| `FollowingUser` | 关注用户（mid, uname, sign） |
| `UpCardInfo` | UP 主卡片信息（mid, name, level, follower_count, official_verify；`official_verify`：-1=无认证, 0=个人认证, 1=企业/机构认证）。`BilibiliAPIClient.get_up_card(mid)` 调用 `/x/space/acc/info`，带 24h TTL 缓存，供 QualityGate 质量门禁使用 |
| `CommentInfo` | 评论（mid, uname, message, like_count） |
| `AuthStatus` | 认证状态（has_cookie, authenticated, username 等；`network_error=True` 表示校验死在传输层——代理 / 风控 / 超时——而非 Cookie 失效） |
| `BilibiliAuthExpiredError` | 任意 `_get_json()` endpoint 返回 `-101` 时的专项异常，仍继承 `BilibiliAPIError` |
| `BilibiliFavoriteDuplicateError` | 仅最终 `/x/v3/fav/resource/deal` POST 返回 `11201` 时产生的安全 operation-tagged 异常；folder/resolver 同码不会变成 duplicate success。 |

`BilibiliAPIError.code` 保存可供 adapter 判定的数值 application code。认证 POST 在网络前通过 `SimpleCookie` 同时校验 `SESSDATA` 与 `bili_jct`；缺失时抛 `BilibiliAuthExpiredError(code=-101)`。GET/POST 共用脱敏 HTTP mapping，只暴露 method、endpoint 与安全 code，不复制 request/response body/message，也不输出 Cookie 或 CSRF；HTTP 412/429 分别转换成 `-412/-429` 并保留原始 HTTP exception 作为 cause。`-101` 在所有 `_get_json()` 路径统一使用固定脱敏异常/日志，不再只特判 `/nav`。

## 配置项

```toml
[bilibili]
auth_method = "cookie"  # "cookie" | "qrcode" | "none"
cookie = ""

[bilibili.browser]
executable = ""    # 留空使用全局安装的 agent-browser
headed = false     # 调试时设为 true
```

## 设计决策

1. **API 优先**：所有能通过 API 完成的操作走 API，browser 仅作备选
2. **统一请求助手 `_get_json()`**：收敛 HTTP 错误映射 + code≠0 检查 + 限流，并允许少量按请求覆盖 headers
3. **轻量限流**：per-client 最小间隔 0.2s，不做全局令牌桶
4. **Protocol DI**：`AuthManager` 通过 `api_client_factory` 注入 API 客户端，测试友好
5. **运行时优先级**：命令和本地服务优先使用显式配置的 cookie；若未配置，则自动回退到 `auth login` 已保存的 cookie，避免首次登录后还要重复把 cookie 写进 `config.toml`
6. **后端可主动请求扩展同步**：`/api/runtime-stream?client=background` 连接建立时，如果 `resolve_runtime_cookie()` 解析不到有效 Cookie，后端会先发 `bilibili_cookie_sync_requested`，扩展收到后立即 POST 当前浏览器 Cookie 到 `/api/bilibili/cookie`
7. **账户侧长期信号分层**：`history / favorites / following` 作为低频同步来源，用来补插件实时事件看不到的长期偏好变化
8. **搜索 WBI 对齐 + 保守降级**：B 站搜索已切到 WBI 路径；客户端现在会复用 `nav` 的 WBI key 对齐浏览器搜索链路，剩余 `412` / `v_voucher` 再降级为空结果，避免把单次 search 失败放大成整轮 refresh 错误
9. **Cookie 过期显式化**：所有 `_get_json()` endpoint 的 `-101` 都与普通业务错误分开处理，日志和异常文本使用固定 session expired / re-auth 提示，不复制远端 message/body；上层仍可按 `BilibiliAPIError` 统一兜底
10. **进程级 search 冷却（分级）**：`BilibiliAPIClient.search()` 把 412 与 `v_voucher` 拆开处理——412 即时硬冷却（base 600s）；`v_voucher` 走 `_record_voucher_block()` 阈值化，连续 `_SEARCH_VOUCHER_BLOCK_THRESHOLD`（默认 3）个关键词耗尽才设共享 cooldown（base 180s），单个被风控的关键词不再让整轮 search + explore 归零十几分钟，`_reset_search_cooldown_backoff()` 在任一成功时清零 streak 与升级档位。dedicated search clients 和主 runtime client 仍通过 `search_cooldown_remaining()` 共享同一状态
11. **扩展兜底只做冷却时补位**：B 站 API 搜索仍是主路径；后端搜索任务只在服务端搜索冷却且浏览器 presence 在线时触发，避免常驻打开搜索页或把插件变成主 crawler。扩展侧只抓用户真实会话中可见的渲染结果，不在 isolated world 里伪造签名请求；background 对 `BILI_TASK_EXECUTE` 做短重试以吸收 content script 注入时序抖动；回传结果也不直接入正式池，而是进入统一候选待评估池，继续复用跨源评估、去重和 admission 规则。
12. **账号写入 fail closed**：收藏/稍后再看在视频信息 lookup 前先验证 `SESSDATA + bili_jct`；BV → aid 通过 `_get_json()` 解析 application code，只有非 bool 的正整数 aid 才允许发写 POST。只有最终 favorite resource-deal POST 的 `11201` 会被 client 包装成 `BilibiliFavoriteDuplicateError`，adapter 还要求 resolved action 为 favorite 才映射 `already_synced`；folder/resolver 的 generic `11201` 及非 favorite route 的 duplicate 异常保持 failed。watch-later 的 `90003` 是视频不可用并固定返回 failed。
13. **收藏夹并发单飞范围**：每个 `BilibiliAPIClient` 按 exact title 持有实例内 `asyncio.Lock`；进入锁后重新查询再决定创建，只避免同一个 client 实例内的并发调用重复创建 `OpenBiliClaw`。不同 client（即使使用同一账号）、不同进程或 event loop 之间没有协调；不宣称 account/process 级全局单飞。
14. **近期 lane 不扩张请求预算**：生产 composition 只把既有第 1 个 search query 改为 `pubdate` 并收窄到 5 条，不复制关键词、不新建 source strategy，也不提高候选 admission 配额；其 provenance 只用于回放与诊断，避免把“较新”误当成“更相关”。
