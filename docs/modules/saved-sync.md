# 原生保存同步

## 概述

`src/openbiliclaw/saved_sync/` 提供平台无关的收藏 / 稍后再看基础设施。它把本地保存和平台账号写入分成两个阶段：本地 membership 必须先提交成功，之后才允许创建原生同步任务。平台失败只更新逐项同步状态，不回滚本地保存。

当前模块已经实现 canonical identity / typed contracts、capability router、local-first sync service、SQLite DAO 边界、首个生产实现 `BilibiliNativeSaveAdapter`、平台中立 HTTP API / runtime 注册，以及插件 side panel、插件设置、桌面 Web、移动 Web 的保存与同步界面。旧端点仍只做本地 B 站兼容保存，不会因本次 wiring 自动修改平台账号。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| Canonical 保存身份 | ✅ | `SavedItemInput.item_key` 使用规范化的 `source_platform:content_id`；B 站 legacy storage key 兼容由 identity / storage 层处理。 |
| Capability router | ✅ | `NativeSaveRouter` 按 canonical 平台注册 adapter；`favorite` 只路由到 native favorite，`watch_later` 优先 native watch-later，不支持时仅在 favorite 可用时回退。adapter 的 `target_label()` 运行时返回必须是去空白后 1–256 字符且无控制字符的字符串，否则逐项安全失败且不会写 route / 调用平台。 |
| Local-first 保存 | ✅ | `SavedSyncService.save_local()` 先提交 membership；自动同步关闭时只落 `pending` native state，不调用 adapter。 |
| GitHub / 微博显式 local-only | ✅ | 两个平台在 canonical source registry 中固定为 `local_only_source`：本地收藏 / 稍后看照常提交 membership，但 native state 当场进入不可重试终态，不创建 `native_save_tasks` 或扩展 broker job。GitHub repository identity 固定为 `github:repository:<numeric-id>`，绝不把本地收藏映射成 GitHub Star。 |
| 持久化同步任务 | ✅ | 自动 / 手动触发统一经 `create_sync_task()` 生成一个非空 UUID，并在单个 `BEGIN IMMEDIATE` 事务中写入 `native_save_tasks` / `native_save_task_items` 快照，同时 claim eligible 项。显式选择按调用者顺序写入不可变 `ordinal`；缺失、已完成和已有 owner 的选择也会得到稳定的 terminal 快照；空的 all-eligible 请求仍持久化零项 task。执行前另以唯一 `task_runner_id` 原子领取 batch runner。 |
| 六平台扩展保存 adapter 与 durable broker | ✅（6/6 executor + 真实账号验证） | YouTube / 小红书 / 抖音 / X / 知乎 / Reddit 的 production adapter 已注册并委托稳定 `ExtensionNativeSaveBroker`，job 写入独立 `extension_native_save_jobs` ledger；`owns(task_id)` 继续提供 global native ownership。YouTube 使用 exact `OpenBiliClaw` / `YouTube Watch Later`；其余五个平台的 watch-later 按能力矩阵回退 favorite。YouTube 同名重复列表按 checked proof、再按稳定 DOM 顺序安全复用，不删除列表。2026-08-31 的真实页面复核确认知乎已改为条目级全局 `收藏 / 已收藏` 开关，因此目标为 `知乎收藏`、不再宣称命名收藏夹；初始“已收藏”只读返回 `already_synced`，绝不反向点击。 |
| 不确定写入的只读确认与 callback 幂等 | ✅ | 抖音 / 小红书 / YouTube / 知乎只有在 mutation 返回 `native_confirmation_not_observed` 时才进入同一 job 的 read-only continuation。runner 终止旧 sender、reload exact URL，并以新 document generation 只读核对 persisted target state；知乎只读取全局“已收藏”状态，不点击任何控件。只有 `already_synced` 正面证据可升级原结果，`synced`、登录/限流/控件错误、超时和异常均保留原不确定失败。任务结果 POST 必须收到 2xx；断线/非 2xx 在独立总 deadline 内以同一对象最多重试 3 次。数据库只对 task/slug/item/status/code/canonical message 完全一致的 terminal replay 返回成功，任何变化仍冲突，既能修复“服务端已落盘但首个 2xx 丢失”，又不会允许重写终态。 |
| 批量逐项执行 | ✅ | `run_sync_task()` 只读取该 task ID 仍存在的 membership，按平台分组、同平台严格按任务快照 `ordinal` 串行执行，并以 `execution_id` 原子 claim / 完成每一项；membership 的 `added_at` 不会改变显式批次顺序，同一任务的并发 runner 不会重复调用 adapter，平台组之间仍可并行。执行中每 30 秒 owner-fenced heartbeat；heartbeat、broker poll 与 terminal persistence 均在线程中的独立短连接上执行，SQLite lock 有界退避重试，durable terminal row 在 heartbeat completion race 中优先。240 秒是调用方响应 deadline，不假定能强制终止不遵守 cancellation 的底层 I/O。 |
| 可恢复任务查询 | ✅ | `get_sync_task()` 从独立 task/item ledger 重建结果，不依赖当前 membership 或可变的 `native_save_states.task_id`。删除本地 membership、service/API 重建后仍可按 UUID 查询同一批逐项快照；未知 UUID 在 HTTP 层返回 404。 |
| 安全失败归一化 | ✅ | 未注册路由写为 `unsupported/unsupported_adapter_missing`，仅该组合可在 adapter 到位后重新快照；executor 返回的 `unsupported_content_type` 等真实内容限制保持 local-only 终态。malformed target / result 写固定 `failed/invalid_adapter_result`；adapter 异常写 `failed/adapter_exception`。 |
| B 站原生 adapter | ✅ | favorite 精确复用或创建 `OpenBiliClaw` 收藏夹；watch-later 写 B 站稍后再看。任意 endpoint 的 `-101` → `login_required`；只有最终 favorite resource-deal POST 的 `11201` 会由 client 标记为 dedicated duplicate，且 adapter 仍要求 resolved action 为 favorite 才映射 `already_synced`；folder/resolver 的同码与非 favorite route 的该异常均为 `failed`；watch-later 的 `90003` 固定为 `failed/bilibili_video_unavailable`。 |
| 平台中立 HTTP API | ✅ | `/api/saved/{list_kind}` 提供 save/list/remove/status/sync，`/api/saved-sync/tasks/{task_id}` 返回 durable 逐项结果；`list_kind`、canonical key、选择和 UUID 均 fail closed。普通稳定键严格为 `<canonical-platform>:<nonblank-stable-id>`；知乎真实内容 ID 额外只放行 `zhihu:question/answer/article:<numeric-id>`，GitHub 额外只放行 `github:repository:<positive-numeric-id>`，Linux.do 额外只放行 `linuxdo:topic:<positive-numeric-id>`；未知类型、空段和其它平台的额外冒号仍拒绝。URL fallback 严格为 `<platform>:url:<24位小写十六进制>`，URL 只接受无凭据、无空白/控制字符且 host/port 有效的 HTTP(S)。入站 `SavedItemIn.content_url` / `cover_url` 会把上游常见的协议相对地址 `//host/path` 归一化为 `https://...` 后再做同一套校验并入库（issue #237）。缺失 membership 只返回安全的 `failed/not_saved_locally`。 |
| 三个图形化保存界面 + CLI 配置可见（现统一为三端内容库子项） | ✅ | 插件 side panel、移动 Web、桌面 Web 都把「稍后再看 / 收藏 / 历史记录」收进单一内容库入口；两个保存子项复用原有列表与状态实现，不复制 API 或平台路由矩阵，旧直达链接会迁移到对应子项。推荐卡只对本地保存做 optimistic update；状态与并发 fence 按 `list_kind:item_key` 隔离，迟到 status 水合不能覆盖新 mutation。保存列表均由后端 `sync_status / sync_task_id / resolved_target / error_code` 驱动：`pending + 空 sync_task_id` 表示自动同步关闭后的本地待同步，保留手动按钮；`pending + 非空 sync_task_id` 与 `syncing` 显示可访问的同步中状态并禁用重复提交。只有 `unsupported_content_type` 显示「仅本地保存」且无同步按钮；历史 `unsupported_adapter_missing` 作为滚动升级兼容状态保留重试；`extension_required` 给出连接登录态插件指引，登录、限流和失败状态给出可操作重试文案，成功态原样显示后端 `resolved_target`。插件 / 移动 saved 与 config 请求、桌面 saved 请求均有有界 timeout；插件的单个 deadline 覆盖初次设备会话交换、401 强制换票与受保护请求。列表成功加载后从持久化 `sync_task_id` 去重恢复任务，task→item ownership 排除重复提交；页面重新可见时恢复查询，销毁时清理 tracker。刷新失败保留最后成功快照并显示重试。保存卡封面在真实 403、网络错误或已缓存失败时移除破图 `<img>`，三端都在原尺寸位置显示 SVG 占位并保留卡片按钮的可访问名称。批量同步与重试加载先捕获列表级焦点并在重渲染后优先还原同一动作；Task/result 文本先清洗再用 textContent/转义渲染；本地删除只调用 `/remove`。桌面 Web 首屏仍可并行水合 `watch_later` / `favorite` 数量，响应 `total` 分别更新内容库的两个子项徽标（不相加）；数量为零或读取失败时保持隐藏，进入子项后的完整刷新以 per-list generation fence 阻止迟到首屏响应覆盖新徽标。CLI 仅由 `config-show` 展示自动同步开关，不提供保存 / 同步动作。 |
| 本地移除历史与恢复（issue #112） | ✅ | `remove_saved_membership()` 删除 normalized membership 时，在同一 SQLite 事务写入 `saved_item_removals` metadata snapshot；不会反向删除平台保存，也不会复制 native task ledger。三端「最近移除」固定只投影 30 天窗口；物理快照在数据库初始化和后续 membership 删除时 opportunistic prune，不宣称存在独立周期任务保证到点即删。同一 canonical `item_key` 的 favorite / watch_later / dismiss / dislike 以 `contexts[]` 在一张卡中返回，各 context 只保留自己的最新事实；favorite 与 watch_later 分别按对应 `list_kind + item_key` 计算 restored，用户可通过原有 `POST /api/saved/{list_kind}` 独立恢复。顶层 `context/restored` 只保留为旧客户端兼容投影，新界面不得用它代替完整 contexts。 |
| 保存卡片反馈操作（issue #111，三个图形界面） | ✅（桌面 Web + 移动 Web + 插件 popup;CLI 无卡片 UI 排除） | 桌面 Web 的「稍后再看 / 我的收藏」列表卡片复用推荐卡反馈操作条(喜欢 / 不感兴趣 / 忽略 / 稍后再看 / 收藏 / 聊一聊):共享 `cardFeedbackBarHtml()` 渲染、`wireSavedCardFeedback()` 接线。稍后再看 / 收藏走共享 `handleCardAction`(`item_key` 维度、所属列表按钮默认 `aria-pressed=true`、取消即 reload 移出、跨列表加另一清单、`setSaved` 与推荐卡状态同步);喜欢 / 不感兴趣 / 忽略 / 聊一聊因 saved 项无 `recommendation_id`(推荐维度 `/api/feedback` 会 404),改走内容维度信号 `/api/events`(与扩展原生 like/dislike 同通道,事件 `type=feedback` + `metadata.feedback_type`,不带 `recommendation_id`)。三端(桌面 / 移动 `web/js/saved.js` / 插件 popup)统一实现:一行低调幽灵图标(喜欢 / 不感兴趣 / 聊一聊 靠左)+ 单个「跨列表」保存 toggle(靠右),经 ui-ux-pro-max 去冗——删掉与「移除」重复的所属 toggle 与桌面 dismiss,所属成员由「移除」管理;喜欢/不感兴趣/聊一聊走 `/api/events` 内容信号,跨列表 toggle 复用各端 saved 注册表。三端均真机 E2E 验证(桌面/移动走隔离后端,popup 真机加载扩展)。 |
| Runtime wiring | ✅ | `RuntimeContext` 一次创建稳定 broker，并在 local/degraded 与每次 config rebuild 注册六平台 adapter；热重载替换 router/service 与 Bilibili client，但保留同一 broker。`BilibiliNativeSaveAdapter` 仍是唯一 direct adapter。broker best-effort 发布 `<slug>_task_available`；无 event hub 的测试/降级构造仍可用。六平台 pending job 随调用方取消安全变为 `cancelled`；已 claim 的 `in_progress` job 则继续等 durable 终态，由原 service watchdog 跨 rebuild 持有 native state heartbeat，不重放平台 mutation。 |

### 六平台目标与授权 E2E

| 平台 | favorite | watch-later |
| --- | --- | --- |
| YouTube | exact `OpenBiliClaw` playlist | `YouTube Watch Later` |
| 小红书 | `小红书收藏` | `小红书收藏` fallback |
| 抖音 | `抖音收藏` | `抖音收藏` fallback |
| X/Twitter | `X Bookmarks` | `X Bookmarks` fallback |
| 知乎 | exact `OpenBiliClaw` collection | 同 collection fallback |
| Reddit | `Reddit Saved` | `Reddit Saved` fallback |

自动同步保持 `auto_sync_enabled = false`；手动 `/sync` 是独立的当次账号写入触发。
真实写入必须使用[六平台授权 E2E runbook](../testing/six-platform-native-save-e2e.md)，为
exact platform / action / public `content_id` / `expected_target` 取得当前**精确命名授权**，
并同时显式设置 `allow_state_changing=true`。授权 envelope 只接受五个固定字段，结果只记录
`platform/action/content_id/expected_target/task_status/error_code`；账号 ID、Cookie、账号凭据、
HTML、响应正文和完整 URL 均 fail closed；小红书公开笔记导航参数只存在于 validated membership/job。
删除本地 membership 只调用 `/remove`，即
**本地删除不反向删除平台保存**。

trusted-local `POST /api/extension/e2e/run` 的 dedicated native-save 模式只接受一个 exact
`native_save_authorization`，且与 generic `actions` 互斥。后端把 envelope 经 runtime stream
交给已安装扩展之前，先读取 exact list membership，并用其 platform/content ID/content type/
canonical HTTPS content URL 以及 production router 重建身份与目标；缺失或 account/profile URL
固定 422 且不 publish。canonical URL 校验与六平台 production executor 等价：拒绝凭据、显式
端口、fragment、多余 query/path segment/host，并校验 watch-later fallback 的 exact
`resolved_action=favorite`。扩展只用已验证 envelope 构造单一 canonical `item_key`，调用对应
`/api/saved/{favorite|watch_later}/sync` 并轮询同一个 durable task。创建与轮询结果都必须
恰好包含该 item，resolved action/target 也必须匹配，随后才通过 dedicated callback 返回
六字段安全结果。初始真实 `pending` snapshot 允许 route 尚未填充的空 target，并继续轮询；
只有 terminal success 才要求 exact target。总 run deadline 为执行保留 1 秒 callback margin；
endpoint 解析、设备会话/401 换票、HTTP 和按剩余时间截断的 poll sleep 都计入各自 deadline；
超时或相关性不确定时只记录 `pending`/`syncing`，不谎报 failed 或暗示可安全重试。通用
`OBC_E2E_EXECUTE` 永不执行 native-save mutation。

## 公开 API

### Adapter protocol 与路由

```python
from openbiliclaw.saved_sync.router import NativeSaveAdapter, NativeSaveRouter

router = NativeSaveRouter([adapter])
adapter, route = router.route("reddit", "watch_later")
```

`NativeSaveAdapter` 暴露：

- `capability: NativeSaveCapability`
- `target_label(action) -> str`
- `async save(item, route) -> NativeSaveResult`

Router 不读取配置或存储。未注册平台、缺失 favorite 能力，或既无 watch-later 也无 favorite fallback 时抛出 `ValueError`；service 会把它转换为逐项 `unsupported/unsupported_adapter_missing` 结果。

### Extension native-save broker

```python
from openbiliclaw.saved_sync.extension_broker import (
    ExtensionNativeSaveBroker,
    ExtensionNativeSaveJob,
    ExtensionNativeSaveResultIn,
)

broker = ExtensionNativeSaveBroker(database, wake_platform=wake_platform)
job_id = broker.enqueue(item, route)
job = broker.claim_next("reddit")
if job is not None:
    broker.submit_result(
        "reddit",
        ExtensionNativeSaveResultIn(job.job_id, job.item_key, "synced")
    )
```

- `ExtensionNativeSaveJob` 只包含 UUID、canonical platform/slug/item identity、清洗后的 allow-listed HTTPS URL、content type、requested/resolved action 与 target label；不包含标题、Cookie、账号凭据、HTML 或响应正文。URL 查询默认剥离；YouTube 仅保留 `v`，小红书仅保留已存在于 saved membership、用于打开公开笔记的单值非空 `xsec_token/xsec_source`，且它们不进入授权或结果记录。
- `ExtensionNativeSaveResultIn` 只接受计划明确的 status/code 组合。扩展传入的 message 永不原样持久化；SQLite 只写后端自有固定文案，并拒绝 Unicode category-C 字符。
- `save()` 的一个 dispatch deadline 同时覆盖 best-effort wake 与 pending poll；wake 挂起或抛错不会越过 deadline。claim 后改用 execution lease，超时结果不会重新进入 pending。
- `/api/sources/{xhs,dy,yt,x,zhihu,reddit}` 共用 `next-task`、`task-result` 与 `kick`；领取 broker job 时返回 `type: native_save` 和脱敏 canonical 字段。结果在 broker/DAO 层严格关联 `platform_slug + task_id + item_key`，格式错误返回 422；首个 canonical terminal 写入成功，完全相同的 terminal replay 幂等返回 2xx，跨 source、改变 status/code/canonical message 的冲突或晚回调仍返回 409。
- Native job 优先于同源 discovery/bootstrap queue；路由先查 global ownership，任何 native UUID 都不进入任一 legacy namespace，再查 exact slug 决定提交或 409。全局未归属 ID 只有在当前 legacy queue 实际拥有该 ID 时才进入旧 handler。X 目前只有 native queue 形态。XHS alarm 与 runtime-stream wake 共用 single-flight poll，手动 native-save 在无精确复用页时可越过后台 discovery tab mutex 打开 exact tokenized note route；executor 的 identity/control fence 仍保证只操作目标内容。production adapter/broker 与六个平台 executor 已全部接入；知乎当前使用全局 `知乎收藏`，不再把命名 `OpenBiliClaw` 收藏夹列为能力。未经用户对命名测试内容明确授权，不执行或重试新的真实账号写入。

### B 站 adapter

```python
from openbiliclaw.bilibili import BilibiliAPIClient
from openbiliclaw.saved_sync.adapters import BilibiliNativeSaveAdapter

client = BilibiliAPIClient(cookie="SESSDATA=...; bili_jct=...")
adapter = BilibiliNativeSaveAdapter(client)
```

- `favorite` 的真实目标是 `B站 OpenBiliClaw 收藏夹`，按 exact title 复用后调用 B 站收藏 resource endpoint。
- `watch_later` 的真实目标是 `B站稍后再看`，不经过 favorite fallback。
- `SESSDATA` 与 `bili_jct` 缺任一项都会在任何视频 lookup / POST 前返回 `login_required`；Cookie、CSRF、服务端 message/body 不会进入 `NativeSaveResult`。
- BV → aid 使用 application-code-aware GET，aid 必须是非 bool 的正整数才允许写 POST。GET/POST 共用脱敏 transport mapping；HTTP 412/429 分别保留为安全数值 code `-412/-429`（保留异常 cause）并归一化为 `rate_limited`。
- 同一个 client 实例内、同一 title 的收藏夹 ensure 由实例内 async lock 串行；锁内重新查询 exact title，因此仅保证该实例内的竞争调用创建一次。不同 client（即使代表同一账号）、不同进程或不同 event loop 之间不协调。
- `RuntimeContext` 已把本 adapter 绑定到当前 B 站 client。只有开启默认关闭的自动同步，或显式调用手动 `/sync`，才会执行账号写入；旧 `/api/watch-later`、`/api/favorites` POST 固定 `auto_sync=False`。

### 平台中立 HTTP API

| 方法 | 路径 | 语义 |
|------|------|------|
| `POST` | `/api/saved/{favorite|watch_later}` | 严格 canonical identity 本地 upsert；按运行时 `saved_sync.auto_sync_enabled` 决定是否只创建后台任务，响应不等待平台 I/O。 |
| `GET` | `/api/saved/{favorite|watch_later}` | 分页返回 metadata snapshot、membership 和最新 native state。 |
| `POST` | `/api/saved/{favorite|watch_later}/remove` | 用 exact `item_key` 只删本地 membership，不反向取消平台保存。 |
| `GET` | `/api/saved/{favorite|watch_later}/status?item_key=...` | 查询单项本地 / 同步状态。 |
| `POST` | `/api/saved/{favorite|watch_later}/sync` | 手动同步；`item_keys=[]` 表示全部 eligible，且始终无视自动同步开关。 |
| `GET` | `/api/saved-sync/tasks/{uuid}` | 轮询持久化逐项状态；已存在的零项 task 返回 200，未知 UUID 返回 404；`login_required` / `rate_limited` / `failed` 不包装成泛化成功。 |

所有 `/api/*` 路径继续受现有 API auth middleware 保护。所有 adapter-controlled `resolved_target/error_code/error_message` 在进入 task poll、单项 status、通用列表和 legacy 列表/state 响应前，都会先移除全部 Unicode category-C 字符，再按字段上限截断。旧 `/api/watch-later` 与 `/api/favorites` 保留 B 站 `bvid` 契约，state/list 响应增量返回 identity、sync 与脱敏后的逐项结果字段；POST 通过 service 本地保存但永不自动同步。

### Local-first service

```python
service = SavedSyncService(database, router, task_starter=starter)
local = service.save_local("watch_later", item, auto_sync=False)
created = service.create_sync_task("watch_later", [item.item_key], "manual_single")
result = await service.run_sync_task(created.task_id)
persisted = service.get_sync_task(created.task_id)
```

- `save_local(list_kind, item, note="", auto_sync=False)`：先写本地；首次关闭自动同步时返回 `pending` 且 `sync_task_id=""`。重复保存只更新 membership / 内容快照，不会把既有 terminal 或 active native state 降级或改写 owner；若原自动同步仍有 active owner，新 save 响应使用新 no-op task 的完整 `failed/sync_already_in_progress` 快照，不与旧 membership 的 route/error 字段拼接。
- `create_sync_task(list_kind, item_keys, trigger)`：真正的空 `item_keys` 表示该列表全部 eligible 项；非空但全部为空白的选择会 fail closed。每次调用都先持久化 task row 和不可变的 item 集合，显式选择的 caller order 同步固化为 `native_save_task_items.ordinal` 并作为同平台执行顺序：显式缺失项写 `failed/not_saved_locally`，terminal 项复制现状，已有 live owner 写 `failed/sync_already_in_progress`，只有新 claim 项进入 `pending` 并启动 runner。若 `task_starter(name, coro)` 登记失败，刚 claim 的 pending owner 与 task ledger 会先回滚清理再重新抛错。
- `run_sync_task(task_id)`：先原子领取唯一 runner token；领取失败只返回 durable snapshot。task heartbeat 与 work 使用 `FIRST_COMPLETED` 监控，task heartbeat 失败立即取消 work并 owner-fenced 释放余项。adapter 在 item heartbeat 异常、响应 deadline 或调用方取消后仍存活时，独立 watchdog 统一持续重试 execution heartbeat，并在 late 结果落库后自清理。进程崩溃才由 poll / 下一次创建按 5 分钟 lease 回收。
- `get_sync_task(task_id)`：已有 task 从 `native_save_task_items` 返回持久化逐项结果；service 用 `has_sync_task()` 区分未知 task 与合法零项 task，HTTP 对前者返回 404。空白 `task_id` 在 service 与 DAO 两层都 fail closed，既不会聚合未领取 pending 行，也不会触发 adapter。

### Task ledger 保留策略

当前版本把已经返回给调用方的 `native_save_tasks` / `native_save_task_items` 保留到该 SQLite 数据库被用户删除或重建为止，**没有 TTL、容量上限或自动 pruning**；唯一立即删除的是 task starter 注册失败、从未返回给调用方的任务。这样可保证现阶段 durable polling 不会因后台清理变成 404，但长期运行的账本会持续增长。有界保留窗口、容量阈值与 active/recent task 保护规则尚未在计划中定义，因此作为后续存储治理项延期，不在本任务中发明破坏性过期策略。

## 数据流与边界

```text
SavedItemInput
  -> POST /api/saved/{list_kind}  # strict identity; local response first
  -> Database.upsert_saved_membership()  # 本地事务先提交
  -> Database.create_native_sync_task_snapshot() # task/items + live claims, one transaction
  -> injected task_starter                    # top-level runner only
  -> SavedSyncService.run_sync_task()
  -> Database.claim_native_save_item(execution_id)      # atomic item ownership
  -> NativeSaveRouter.route()
  -> Database.update_native_save_claim_route()           # owner fence
  -> BilibiliNativeSaveAdapter.save()                     # direct Bilibili branch
     OR ExtensionNativeSaveBroker                         # six extension platforms
  -> extension_native_save_jobs -> /api/sources/<slug>/next-task -> installed extension
  -> authenticated task-result + owner heartbeat/deadline
  -> Database.complete_native_save_claim(item result)   # state + task snapshot, one transaction
  -> SavedSyncService.get_sync_task()
  -> GET /api/saved-sync/tasks/{task_id} # truthful per-item polling
  -> UI list reload recovers sync_task_id # deduped tracker + item ownership
```

删除本地 membership 仍只由 storage/API 层负责，不会反向删除平台账号内容。B 站 adapter 不读取配置、HTTP request 或全局 runtime，也不自行重试；平台中立 service 继续拥有任务、route 与持久化状态，API route 只在保存请求当下读取当前热重载配置。

验证边界同样分层：自动化和默认 smoke 只使用 mock adapter 或本地 membership，不发送任何
平台 favorite / watch-later 请求。Bilibili 的既有 runbook 要求为具体 BV ID 取得用户当次授权；
六平台 runbook 则逐项要求 exact platform/action/public content ID/expected target，并只记录
六字段安全结果。YouTube、小红书、抖音、X、知乎与 Reddit 的 production adapter、runtime
broker registration 及六个平台 executor wiring 已完成；fixture 自动化覆盖当前能力矩阵，
其中知乎目标为条目级全局 `知乎收藏`、所有 verifier 零点击。历史逐平台实号记录只覆盖当时
runbook 中精确命名的公开测试内容，不授权自动重试或
删除平台账号中的保存记录。

2026-07-13 已在当次明确授权下完成 B 站真实账号授权 E2E：手动收藏、手动稍后再看和
开启配置后的自动收藏均返回 `synced`；删除三条本地 membership 后，两条平台收藏与一条
平台稍后再看仍可读，自动同步配置也恢复为测试前的关闭值。该结果只证明 Bilibili direct
adapter 的授权链路，不扩大其它平台边界。
