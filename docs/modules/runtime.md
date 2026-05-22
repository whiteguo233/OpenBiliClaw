# Runtime Module

## 概述

`src/openbiliclaw/runtime/` 负责后端 daemon 的长期运行能力：后台刷新、账号同步、运行时事件流、浏览器插件 presence gate、自动更新和任务生命周期管理。FastAPI 启动后会通过 `RuntimeContext` 持有这些 runtime 服务，配置热重载时重建可替换组件。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 后台刷新控制 | ✅ | `ContinuousRefreshController` 按 scheduler 配置补充候选池，并通过 source policy 计算各平台有效配比。 |
| 候选池文案预计算状态同步 | ✅ | 独立 `_loop_pool_precompute()` 将 fresh 候选补齐 `pool_expression` / `pool_topic_label` 后，会同步更新 `last_replenished_count` 并推送 `refresh.pool_updated`，避免前端在库存已可用时仍显示“没补进”。 |
| embedding 后台预热 | ✅ | refresh 完成前只保证候选入池与文案可用；`prewarm_supergroup_embeddings()` / `prewarm_pool_mmr_embeddings()` 作为后台 task 运行，慢本地 embedding 后端不会占住 refresh lock 或让界面长时间停在“正在补货”。 |
| YouTube 后台 discovery producer | ✅ | `YoutubeDiscoveryProducer` 独立运行 `yt_search` / `yt_trending` / `yt_channel`，只在 YouTube 平台族低于 quota 时由 `_loop_youtube_producer()` tick，按每日 ledger 和 `min_interval_minutes` 控制执行。 |
| 运行时频率配置 | ✅ | `refresh_check_interval_seconds`、行为触发阈值、trending / explore 间隔、单轮发现上限、主动推送间隔和 speculator idle tick 都从 `[scheduler]` 读取，配置热重载后重建 runtime 生效。 |
| 浏览器 presence gate | ✅ | `background_llm_work_allowed()` 结合 `scheduler.enabled` 与 `pause_on_extension_disconnect` 控制 daemon-owned 后台 LLM / embedding 工作。 |
| Runtime event stream | ✅ | `/api/runtime-stream` 向扩展推送状态、Cookie sync 请求、配置重载和 presence 事件。 |
| 图片代理 API | ✅ | `/api/image-proxy` 为移动 Web 和浏览器插件代理白名单 CDN 封面图，逐跳校验 redirect，并在返回前完成类型和 10MB 大小校验。 |
| 自动更新 | ✅ | `AutoUpdateService` 周期性检查 backend git tag，发现新 backend 版本后执行 `git pull --ff-only` 与依赖同步。 |
| 账号同步 | ✅ | `AccountSyncService` 同步 B 站账号历史、收藏和关注等信号；历史按 `view_at + 同秒 bvid 集合` 增量导入，收藏 / 关注只把新增 ID 转成画像事件，避免重放旧信号。 |
| 多源 bootstrap 去重 | ✅ | `/api/sources/{xhs,dy,yt}/task-result` 会用 `source_bootstrap_state.json` 过滤跨任务旧 identity key；任务结果仍完整保留，只有新增项进入 memory / profile pipeline。 |
| 扩展任务 claim / 复用 | ✅ | XHS / 抖音 / YouTube bootstrap 任务在扩展 poll 时用短生命周期 SQLite 连接标记 `in_progress`，CLI 默认复用 6 小时内近期任务，避免重复打开前台 tab 全量扫描，也避免 FastAPI 并发 poll 在共享 connection 上嵌套事务。 |
| Soul 画像自动 bootstrap | ✅ | `AccountSyncService` 首次成功写入账号行为并完成 `analyze_events()` 后，若 soul 画像仍为空，会自动调用 `build_initial_profile([])`；每进程生命周期最多尝试一次。 |
| 降级模式启动 | ✅ | 生产 `create_app()` 遇到 `RegistryBuildError` 时构造 degraded `RuntimeContext`，保留健康检查、配置读取/保存、runtime status 与 runtime stream，方便用户从 popup 修复错误配置。 |
| 配置热重载 LLM override | ✅ | `RuntimeContext._rebuild_components()` 从 config 构造 `module_overrides`，同时注入主 `LLMService` 与 `SoulEngine` 内部 service；热重载后的 speculator tick detached 到 `BackgroundTaskRegistry`，不阻塞 `/api/config` 响应。 |
| 运行日志降噪 | ✅ | 全局 logging 初始化会把 `httpx` / `httpcore` logger 提升到 WARNING，避免文件日志在 DEBUG 模式下被连接细节刷屏；业务模块仍按 `logging.file_level` 输出。 |

## 公开 API

```python
from openbiliclaw.runtime.updater import AutoUpdateService

service = AutoUpdateService(enabled=True, check_interval_hours=6)
result = await service.check_and_update_now()
```

`AutoUpdateService.check_and_update_now()` 返回字典结果：

- `{"checked": False, "reason": "disabled"}`：自动更新关闭。
- `{"checked": True, "updated": False, "reason": "no_backend_tag_yet"}`：GitHub tag 列表中没有可用 backend tag。
- `{"checked": True, "updated": False, "current_version": "...", "remote_version": "..."}`：已是最新 backend 版本。
- `{"checked": True, "updated": True, ...}`：已应用更新并尝试重启当前进程。

### Degraded RuntimeContext

`build_runtime_context()` 仍然保持严格：LLM registry 无法构建时直接抛出 `RegistryBuildError`，方便测试和 CLI 调用方快速失败。FastAPI 生产入口 `create_app()` 会单独捕获这个错误并调用 `build_degraded_runtime_context()`。

降级模式下可用接口：

- `GET /api/health`：返回 `status="degraded"`、`reason="llm_registry_unavailable"` 和 blocking issues；当 `SoulEngine` 可用时会额外返回可选字段 `profile_ready`，表示 soul 画像是否已生成。
- `GET /api/config`：返回完整配置、`degraded=true` 和同一组 issues。
- `PUT /api/config`：允许保存修复配置，但跳过热重载并返回 `restart_required=true`。
- `GET /api/runtime-status` 与 `/api/runtime-stream`：用于 popup 展示降级状态；stream 会先发送 `{type:"degraded", ...}` 并保持连接。

其他 API 在降级模式下返回 503，避免在缺少 LLM registry、数据库/运行时组件不完整时继续执行推荐、发现或画像链路。

### Image Proxy API

`GET /api/image-proxy?url=<encoded_url>` 只代理明确白名单内的 HTTP(S) 图片 URL，用于移动 Web `/m/` 和浏览器插件 side panel 的推荐、惊喜推荐和消息封面图。白名单按域名边界匹配，当前包含 `hdslb.com`、`xhscdn.com`、`pstatp.com`、`douyinpic.com`、`douyinvod.com`、`ytimg.com` 和 `ggpht.com`，会拒绝非 HTTP(S)、缺 hostname、userinfo 和非白名单域名。

代理不使用自动跳转；`301/302/303/307/308` 最多手动跟随 3 次，每一跳都会重新校验目标 URL。上游响应必须是 2xx 且 `Content-Type` 为 `image/*`。若 `Content-Length` 超过 10MB 会立即返回 413；缺失或伪造长度时，响应体会先流式写入 `SpooledTemporaryFile(max_size=1MB)`，实际读取超过 10MB 同样返回 413，避免在下游响应头已发送后才发现超限。

成功响应会带 `Cache-Control: public, max-age=86400` 和 `X-Content-Type-Options: nosniff`，并写入本地图片缓存。缓存回退只用于上游网络失败、超时或 5xx 类上游错误；URL / redirect 白名单失败、非图片 Content-Type、超过 10MB 等校验类错误会保留 403 / 400 / 413 等明确状态，不会被统一折叠成 502。该接口按本地单用户后端设计，默认只应暴露在 `127.0.0.1` 或用户可信局域网；若用 `--host 0.0.0.0` 对外监听，应在反向代理层自行加访问控制。

### AccountSyncService

```python
from openbiliclaw.runtime.account_sync import AccountSyncService

service = AccountSyncService(
    memory_manager=memory,
    bilibili_client=bilibili_client,
    soul_engine=soul_engine,
)
result = await service.sync_now()
```

`sync_now()` 会拉取最近一批 B 站历史、收藏夹和关注列表，但只有新增信号会进入 `memory.propagate_event()` 与 `soul_engine.analyze_events()`：

- 历史记录：使用 `last_history_view_at`、`last_history_bvid` 和 `history_bvids_at_last_view_at` 跳过已经处理过的同秒历史项。
- 收藏夹：使用稳定排序后的 `favorite_signature` 和 `favorite_bvids`，签名变化时只导入新增 bvid。
- 关注列表：使用 `following_signature` 和 `following_mids`，签名变化时只导入新增 mid。

### YoutubeDiscoveryProducer

```python
from openbiliclaw.runtime.youtube_producer import YoutubeDiscoveryProducer

result = await producer.produce_if_due(limit=20)
```

`produce_if_due()` 返回 `{"discovered": int, "reason": str, ...}`。常见 `reason`：

- `ok`：至少完成了一轮可运行策略；结果已通过 `ContentDiscoveryEngine.discover()` 进入统一评估 / 缓存路径。
- `throttled`：距离上次执行未达到 `min_interval_minutes`。
- `budget_exhausted`：当天 `yt_search` / `yt_trending` / `yt_channel` 的执行 ledger 已耗尽。
- `disabled` / `no_profile` / `error`：分别表示配置关闭、画像不可用或所有策略失败。

### Source Bootstrap Task Results

XHS / 抖音 / YouTube 的插件任务桥保留两层去重：

- 单任务内：`merge_result()` 合并 partial / final payload 时按 scope + 平台原生 ID / URL / title 去重，只把本次新增项返回给 API 传播。
- 跨任务：API 在传播 bootstrap 事件前读取 `source_bootstrap_state.json`，跳过已经进入事件路径的 `xhs_seen_note_keys` / `dy_seen_video_keys` / `yt_seen_item_keys`。这样 `fetch-*`、`init` 或近期任务复用重复返回同一批收藏 / 历史时，不会再次写入 memory 或触发增量画像分析。

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `scheduler.auto_update_enabled` | `false` | 是否启用后台自动更新检查。 |
| `scheduler.auto_update_check_interval_hours` | `6` | 自动更新检查间隔。 |
| `scheduler.enabled` | `true` | 后台 LLM / embedding 总开关。 |
| `scheduler.pause_on_extension_disconnect` | `false` | 浏览器插件断开后是否暂停后台 LLM / embedding 工作。 |
| `scheduler.extension_disconnect_grace_seconds` | `90` | 插件断开后的宽限秒数。 |
| `scheduler.refresh_check_interval_seconds` | `60` | `ContinuousRefreshController` 主循环轮询间隔。 |
| `scheduler.signal_event_threshold` | `6` | 累计多少条新行为事件后触发 `search + related_chain`。 |
| `scheduler.trending_refresh_hours` | `3` | `trending` 策略最小刷新间隔。 |
| `scheduler.explore_refresh_hours` | `12` | `explore` 策略最小刷新间隔。 |
| `scheduler.discovery_limit` | `30` | 单轮 discovery wave 候选上限，最大 `60`。 |
| `scheduler.proactive_push_interval_seconds` | `120` | 主动推荐 / probe 推送循环间隔。 |
| `scheduler.speculator_idle_interval_minutes` | `30` | 画像 pipeline 空闲时检查猜测兴趣生命周期的间隔。 |

## 设计决策

### Auto-update release contract

后端自动更新只认 backend source tag：

- backend 源码更新发布为 git tag：`backend-vX.Y.Z`。
- legacy 安装仍兼容 `vX.Y.Z` 和裸 semver `X.Y.Z`。
- 浏览器扩展 release 使用 `extension-vX.Y.Z`，必须被后端自动更新忽略。
- GitHub `/releases/latest` 当前由扩展 artifact 占用，不能代表后端源码版本；`AutoUpdateService._fetch_latest_version()` 直接查询 `/tags`，分页过滤 backend tag 后选择最高版本。

这样可以避免后端 `0.3.64` 把 `extension-v0.3.24` 解析成 `(0,)` 并误报 "Already up-to-date"。

### Config recovery boundary

配置恢复是 runtime 和 API 的交界：`/api/config` 写盘前先校验新配置可构建 LLM registry，正常模式下写入后调用 `RuntimeContext.rebuild_from_config()` 与 `restart_background_tasks()`。热重载失败会恢复 `config.toml.bak`，并把 `rollback_applied` 返回给调用方；降级模式不做热重载，保存成功后返回 `restart_required=true`，要求用户重启 daemon 让新的 registry 生效。

热重载成功后，所有可替换 LLM 入口都会拿到同一份 `module_overrides_from_config(config)`：

- 主 runtime 的 discovery / recommendation / XHS producer 共用 `ctx.llm_service`。
- SoulEngine 内部的 preference / awareness / insight / profile_builder / speculator / dialogue_insight 使用同一份 override。
- SocraticDialogue fallback 若未显式注入 `llm_service`，会继承 `SoulEngine._module_overrides` 再构造 `LLMService`。

`restart_background_tasks()` 在启动后置 one-shot 时只调度 `_safe_post_reload_speculate()`，不会 await speculator 的 `force_tick()`。这保证 popup 保存配置的 HTTP 响应不被一次画像猜测卡住；异常由 helper 吞掉并记录 debug，下一轮正常调度仍会继续。

刷新调度不使用 `scheduler.discovery_cron`。该字段仅保留为旧配置兼容；实际触发由 `refresh_check_interval_seconds` 轮询、候选池缺口、`signal_event_threshold`、`trending_refresh_hours`、`explore_refresh_hours` 和 `discovery_limit` 共同决定。

`ContinuousRefreshController.run_forever()` 当前并行启动 refresh、pool precompute、soul pipeline、XHS producer、Douyin producer、YouTube producer 和 proactive push 七条 loop。共享的 `background_llm_work_allowed()` gate 覆盖所有 daemon-owned LLM / embedding 工作；YouTube 与 XHS / Douyin 一样会在 gate 关闭时跳过 tick。不同点是 YouTube 不通过扩展任务队列做 steady-state discovery，而是在后端直接调用 YouTube strategies；`yt_tasks` 只保留给 bootstrap profile 导入。
