# B 站接入层

> API 优先的 B 站数据访问层，包含 Cookie 认证、核心 API 封装和 agent-browser 浏览器集成。

## 概述

`bilibili/` 包是系统访问 B 站的唯一出口，分三层：

1. **AuthManager** — Cookie 管理和登录验证
2. **BilibiliAPIClient** — HTTP API 封装（主访问路径）
3. **BilibiliBrowser** — agent-browser CLI 封装（API 无法覆盖的操作）

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 3.1 Cookie 认证 | ✅ | set / load / validate / clear + CLI auth 命令 + 运行时 cookie 回退 |
| 扩展 Cookie 自动同步 | ✅ | 浏览器扩展可 POST `/api/bilibili/cookie` 持久化 Cookie；后端在 background runtime-stream 连接且缺 Cookie 时会发 `bilibili_cookie_sync_requested` 主动要求扩展回传 |
| 3.2 核心 API | ✅ | 10+ API 方法 + 限流 + 统一错误处理 |
| `/nav` 登录态诊断 | ✅ | `/x/web-interface/nav` 返回 `-101` 时抛 `BilibiliAuthExpiredError`，日志明确提示 session expired / 重新登录或保持扩展在线同步 Cookie |
| 搜索 WBI 化与 412 软降级 | ✅ | `search()` 现会先从 `nav` 获取 WBI key，走 `/x/web-interface/wbi/search/type`；遇到 `412 Precondition Failed` 时会记录 warning 并返回空结果，避免拖垮整轮 discover |
| 搜索风控冷却 | ✅ | 连续 `v_voucher` 重试耗尽或 412 后启用进程级 search cooldown；所有 BilibiliAPIClient 实例共享冷却状态，避免 dedicated search client 在下一轮继续撞风控 |
| 账户侧同步来源 | ✅ | 已支持 history / favorites / following 三类长期信号，供后台低频同步使用 |
| 3.3 agent-browser 集成 | ✅ | navigate / get_page_content + CLI browser 命令 |

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

client = BilibiliAPIClient(cookie="SESSDATA=abc", min_request_interval=0.2)

# 认证
try:
    nav = await client.get_nav_info()  # NavInfo(is_login=True, uname="alice", mid=10086)
except BilibiliAuthExpiredError:
    # Cookie 已过期或未登录；重新登录 B 站，或保持浏览器扩展在线自动同步新 Cookie
    raise

# 观看历史（cursor-based 分页）
history = await client.get_user_history(max_items=200)

# 搜索
results = await client.search("纪录片", page=1, order="pubdate")
# search 请求会使用 WBI 签名 + 搜索页 Referer；
# 若 B 站返回 412 或连续 v_voucher，则这里会保守返回 []，
# 并让进程内后续 search 短期冷却。
remaining = client.search_cooldown_remaining()

# 收藏
folders = await client.get_favorite_folders()  # list[FavoriteFolder]
all_fav = await client.get_all_favorites(max_folders=10, max_items_per_folder=50)

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

### 数据结构

| 类 | 用途 |
|----|------|
| `NavInfo` | 登录用户基本信息（is_login, uname, mid） |
| `VideoInfo` | 视频详情（标题、UP主、播放/点赞/收藏数等） |
| `FavoriteFolder` | 收藏夹元数据（media_id, title, media_count） |
| `FavoriteFolderWithItems` | 收藏夹 + 内容列表 + truncated 标记 |
| `FollowingUser` | 关注用户（mid, uname, sign） |
| `CommentInfo` | 评论（mid, uname, message, like_count） |
| `AuthStatus` | 认证状态（has_cookie, authenticated, username 等） |
| `BilibiliAuthExpiredError` | `/nav` 返回 `-101` 时的专项异常，仍继承 `BilibiliAPIError` |

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
9. **Cookie 过期显式化**：`/nav` 的 `-101` 与普通业务错误分开处理，日志和异常文本都包含 session expired / re-auth 提示；上层仍可按 `BilibiliAPIError` 统一兜底
10. **进程级 search 冷却**：`BilibiliAPIClient.search()` 在连续 `v_voucher` 重试耗尽或 412 时会设置共享 cooldown；dedicated search clients 和主 runtime client 都会通过 `search_cooldown_remaining()` 看到同一状态，避免一分钟一轮持续打穿 B 站风控桶
