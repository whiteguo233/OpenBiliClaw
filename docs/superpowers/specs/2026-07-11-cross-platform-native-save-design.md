# Cross-Platform Native Save Design

> **Current-state correction (2026-08-31):** the implemented Zhihu adapter targets the
> item-level global `知乎收藏` state. The earlier named-collection fallback below is superseded;
> an initially `已收藏` control and all verification-only paths are zero-click.

## Goal

把 issue #56 从单一的 B 站「全部稍后看」诉求收敛成一套可扩展的跨平台保存契约，同时保留其核心体验：用户在 OpenBiliClaw 中点击「收藏」或「稍后再看」后，内容一定先进入本地列表；用户明确允许时，再同步到来源平台账号，方便在平台 App / Web 中继续使用。

本设计覆盖当前七个正式来源：Bilibili、小红书、抖音、YouTube、X（Twitter）、知乎和 Reddit。平台原生能力不一致，因此产品不假设每个平台都有「稍后再看」：

- 「收藏」始终路由到平台的收藏、书签、Saved 或播放列表能力。
- 「稍后再看」优先路由到平台原生 Watch Later / 稍后观看；平台没有时自动降级到该平台收藏能力。
- 无论平台同步是否启用或成功，OpenBiliClaw 本地记录始终保留。

## Confirmed Product Decisions

- 推荐卡的「收藏」和「稍后再看」都先完成本地保存。
- 新增一个全局「保存时自动同步到对应平台」开关，默认关闭。
- 开关开启后，推荐卡保存会立即创建平台同步任务；同步在后台执行，不阻塞本地保存。
- 开关关闭时，本地记录显示「待同步」，用户可在本地收藏页或稍后再看页手动同步。
- 本地收藏页和稍后再看页的手动同步按钮始终可用，不受自动同步开关影响；点击本身就是本次外部账号写入的明确授权。
- 需要收藏夹或播放列表的平台优先使用名为 `OpenBiliClaw` 的专用容器；不存在时由适配器在首次同步时创建。
- 平台不支持自定义收藏夹、当前账号没有该能力或创建失败后可安全使用默认收藏区时，适配器退化到默认收藏区，并在结果中显示真实目标。
- 平台同步失败不回滚本地保存，也不后台无限重试；用户在本地列表页手动重试。
- 删除本地记录默认不反向取消平台收藏、书签或稍后看，避免产生意外的外部账号删除操作。

## Non-Goals

- 不把所有平台的收藏能力伪装成「官方稍后看」。
- 不保证平台上的每一种内容类型都能收藏；受限制内容必须返回逐项 `unsupported`，不能整批静默失败。
- 不在本项中实现自动定时重试、指数退避后台队列或无人确认的账号状态变更。
- 不在删除本地收藏/稍后看时自动删除平台记录。
- 不要求所有平台使用同一种网络传输；每个适配器选择当前最稳定且最少暴露凭证的路径。
- 不复用通用 E2E 点击器作为生产保存实现；生产适配器必须有稳定的内容定位、登录判断、幂等判断和结构化结果。
- 不在单元测试或默认 smoke 中修改真实平台账号。

## Canonical Local Identity And Storage

当前 `watch_later` / `favorites` 和 `content_cache` 以 `bvid` 作为核心标识，不足以安全表达跨平台内容。新契约使用：

```text
item_key = canonical_source_platform + ":" + canonical_content_id
```

`twitter` 是 X 的 canonical slug；别名只允许在输入边界归一化。URL 只能在平台没有稳定内容 ID 时参与生成哈希 fallback，不能替代已有稳定 ID。

新增规范化保存模型：

- `saved_items`
  - `item_key` 主键
  - `source_platform`
  - `content_id`
  - `content_url`
  - `content_type`
  - `title`
  - `author_name`
  - `cover_url`
  - `created_at` / `updated_at`
- `saved_memberships`
  - `(list_kind, item_key)` 联合主键
  - `list_kind` 仅允许 `favorite` / `watch_later`
  - `note`
  - `added_at`
- `native_save_states`
  - `(list_kind, item_key)` 联合主键
  - `requested_action`
  - `resolved_action`
  - `resolved_target`
  - `status`
  - `task_id`
  - `last_error_code` / `last_error_message`
  - `last_attempt_at` / `synced_at`

保存项持有必要的元数据快照，不依赖 `content_cache` 长期存在或继续以 B 站式主键查询。现有 `watch_later` 和 `favorites` 数据迁移到新表；旧 API 路径继续保留兼容，但内部转换为 `item_key`。无法从旧行恢复平台时按现有兼容语义归为 `bilibili`，并保留原 `bvid` 作为 `content_id`。

统一发现 / 推荐持久化也必须保留同一个 `item_key`：`content_cache` 和 `recommendations` 增加 canonical identity，所有新写入、查询、候选 suppression、反馈和保存元数据关联均以 `item_key` 为准。`bvid` 降为 B 站兼容字段，不再作为跨平台唯一键。旧库迁移先用已有 `source_platform + content_id` 构造 key，缺字段的遗留 B 站行才用 `bilibili:<bvid>`；迁移后必须允许两个平台存在相同裸 content ID。

推荐、惊喜推荐和保存 API 的输入必须携带 `source_platform`、`content_id`、`content_url` 和 `content_type`。B 站客户端仍可只传 `bvid`；兼容层把它规范化成 `bilibili:<bvid>`。非 B 站新客户端不得只传裸 `bvid`。

新的平台中立 API 使用：

- `POST /api/saved/{list_kind}`：本地 upsert，body 携带规范化内容身份和元数据。
- `POST /api/saved/{list_kind}/remove`：body 携带 `item_key`，避免把任意平台 ID 直接放入 path。
- `GET /api/saved/{list_kind}`：分页返回本地项目及 native sync state。
- `GET /api/saved/{list_kind}/status?item_key=...`：查询单项本地和同步状态。
- `POST /api/saved/{list_kind}/sync`：同步指定 `item_keys`；空数组表示同步该列表全部 eligible 项。
- `GET /api/saved-sync/tasks/{task_id}`：查询批量任务的逐项结果。

`list_kind` 只接受 `favorite` / `watch_later`。现有 `/api/watch-later` 和 `/api/favorites` 继续服务旧客户端，但只承诺 B 站 `bvid` 兼容；插件、桌面 Web、移动 Web 三个图形化保存界面统一调用 `/api/saved/*`，CLI 只显示配置。

## Capability Router

平台无关的 `NativeSaveRouter` 只接收用户意图，不包含站点接口细节。每个适配器声明：

- 是否支持 `favorite`。
- 是否支持原生 `watch_later`。
- 是否支持命名收藏夹 / 播放列表。
- 支持的内容类型和稳定 ID 格式。
- 所需登录信号。
- 执行 transport：backend direct、extension task 或两者的受控 fallback。

路由规则固定为：

```text
favorite
  -> native favorite

watch_later
  -> native watch_later, when supported for this item
  -> native favorite, otherwise
```

路由结果必须返回真实的 `resolved_action` 和 `resolved_target`。B 站 canonical 文案采用产品已有术语「B站稍后再看」（替代早期设计稿中的「稍后观看」）；UI 使用 adapter 返回值显示真实目标，不能只显示笼统的「平台同步成功」。

## Platform Mapping

| Platform | Favorite intent | Watch-later intent | Preferred transport |
| --- | --- | --- | --- |
| Bilibili | `OpenBiliClaw` 收藏夹 | 官方稍后再看 | backend direct，复用已验证 Cookie / CSRF |
| YouTube | `OpenBiliClaw` 播放列表 | Watch Later | extension logged-in task；官方 Data API 不再支持 Watch Later 写入 |
| X / Twitter | Bookmark；账号支持且接口稳定时可使用 `OpenBiliClaw` bookmark folder | Bookmark | 官方 OAuth 能力可用时 API-first，否则 extension logged-in task |
| Reddit | Saved；支持分类时使用 `OpenBiliClaw`，否则默认 Saved | Saved | authenticated API / extension task，按真实凭证能力选择 |
| Xiaohongshu | `OpenBiliClaw` 收藏夹；不可用时默认收藏 | 同收藏 | extension logged-in task |
| Douyin | `OpenBiliClaw` 收藏夹；不可用时默认收藏 | 同收藏 | direct cookie path 可稳定写入时使用，否则 extension logged-in task |
| Zhihu | 全局 `知乎收藏` | 同收藏 | extension logged-in task |

适配器必须先验证当前内容类型是否允许目标动作。例如平台禁止某类视频进入播放列表时，只把该项标为 `unsupported`，同批其它项继续执行。

## Sync Task Contract

自动同步和列表页手动同步共用同一编排入口和状态模型，不能各自实现平台判断。后端接收一个或多个 `item_key`、`list_kind` 和触发来源 `auto | manual_single | manual_batch`，执行：

1. 校验本地 membership 存在。
2. 通过 capability router 解析平台目标。
3. 按平台和 resolved target 分组。
4. 对 backend-direct 适配器执行有界批处理。
5. 对 extension-backed 适配器创建平台任务并 kick 已连接扩展。
6. 按项目插件任务规范使用精确端点：
   - `/api/sources/<slug>/next-task`
   - `/api/sources/<slug>/task-result`
   - `/api/sources/<slug>/kick`
7. 持久化每项结果，不因单项失败回滚同批成功项。

任务结果按 item 返回：

- `synced`：新写入成功。
- `already_synced`：平台已存在，按成功处理。
- `pending` / `syncing`：已排队或执行中。
- `login_required`：缺真实登录态或登录已失效。
- `unsupported`：平台或内容类型不支持目标动作。
- `rate_limited`：平台限流，保留供手动重试。
- `failed`：其它可诊断失败，带安全错误码和简短信息。

`already_synced` 与 `synced` 在用户界面都显示「已同步」，但诊断结果保留差异。适配器执行前检查已有状态；平台自身返回重复 / 已存在也必须规范化成 `already_synced`，保证重复点击和重复批量同步幂等。

## User Flows

### Recommendation card

1. 用户点击「收藏」或「稍后再看」。
2. 客户端先调用本地保存 API；本地失败时停止，不创建外部同步任务。
3. 本地成功后按钮立即进入已保存态。
4. 自动同步关闭时，显示「已保存，待同步」。
5. 自动同步开启时，后台创建任务并显示「本地已保存，正在同步」。
6. 任务快速完成时更新为真实平台结果；popup 关闭或结果较慢时，状态由本地列表页继续展示。

### “全部稍后看”

当前按钮只 dismiss 并清空惊喜队列，必须改为：

1. 捕获本批项目的完整平台身份。
2. 批量写入本地 `watch_later` membership。
3. 只在本地保存结果返回后处理队列。
4. 自动同步开启时，为本地保存成功项创建同步任务。
5. 展示「本地保存 N、同步中 N、本地失败 N」；不能在写入前清空。
6. 本地失败项保留在当前队列，允许用户重试；本地成功项可以移出推荐队列，即使平台同步仍 pending。

### Saved pages

收藏页和稍后再看页均提供：

- 页面级「同步未同步内容（N）」按钮。
- 单项「同步」或「重试同步」按钮。
- 平台、真实目标和同步状态展示。
- 分平台批量结果，例如「B站 4/4、YouTube 2/3、小红书 1/1」。

页面级按钮处理 `pending`、`login_required`、`rate_limited`、`failed` 和尚无状态的旧迁移项，不重复提交已经 `synced` 的相同目标。手动同步无视自动同步开关。未连接必需扩展时返回 `extension_required`，页面提示用户打开 / 连接安装了 OpenBiliClaw 插件的登录态浏览器。

## Configuration And Consent

新增配置：

```toml
[saved_sync]
auto_sync_enabled = false
```

该字段通过配置加载、`/api/config` GET/PUT、`config-show`、插件设置、桌面 Web 设置和移动 Web 设置一致 round-trip。旧配置缺字段时默认 `false`。

用户首次开启时，界面明确提示：「开启后，在 OpenBiliClaw 点击收藏或稍后再看会修改对应平台账号中的收藏、书签、Saved、播放列表或稍后观看。」确认后才保存为 true。关闭开关不取消已经同步的平台记录，也不影响列表页手动同步。

手动批量同步按钮在执行前展示项目数和将被修改的平台；用户点击确认即授权本批状态变更。单项同步不增加二次确认。

## Error Handling And Observability

- 本地保存和平台同步是两个独立结果；平台失败绝不回滚本地成功。
- 批量同步用逐项提交语义，允许部分成功。
- 登录失效显示 `login_required`，不得冒充成功或使用历史任务推断登录。
- 平台限流、风控、页面结构变化、收藏夹创建失败和内容不支持使用不同错误码。
- 没有安全降级目标时不得把「创建 OpenBiliClaw 收藏夹失败」悄悄写到未知收藏夹；适配器只有在已声明默认收藏区语义时才能降级。
- 日志记录 task ID、平台、item key、目标、耗时和错误码，不记录 Cookie、CSRF、OAuth token、带 token 的 URL 或平台完整响应。
- 保存状态是服务端权威；客户端 optimistic UI 在本地写入失败时回滚，在平台写入失败时保持本地已保存并显示同步失败。
- 不自动无限重试。用户再次点击列表页同步时创建新 attempt，并保留上一条安全诊断信息。

## Security And Rate Limits

- 平台写入属于状态变更动作，真实 E2E 和手动 smoke 必须使用显式 `allow_state_changing` 或测试账号。
- XHS / Zhihu 等当前只向后端上报登录布尔值的平台继续在浏览器同源环境执行写入，不为本功能把原始 Cookie 上传后端。
- 插件 dispatcher 访问后端必须使用共享鉴权 client，不能裸 `fetch`。
- 每个平台适配器定义串行或小并发上限、项间隔和批次上限；批量同步不能无界并发打开标签页或请求平台接口。
- 任务只能处理用户已保存到本地的明确 item keys，不能接受任意 URL 作为账号写入代理。

## Surface Contract

用户可见行为覆盖四个表面：

- Extension popup：推荐卡即时保存、自动同步提示、收藏/稍后看列表和手动同步。
- Desktop Web：设置开关、收藏/稍后看列表、手动同步和逐项状态。
- Mobile Web：设置开关、收藏/稍后看列表、手动同步和逐项状态；需要扩展时显示连接提示。
- CLI：`config-show` 显示开关，并提供可诊断的同步状态 / smoke 输出；默认 smoke 不执行真实账号写入。

如果某个平台适配器只能由扩展执行，桌面和移动 Web 仍可创建后端任务，但必须等待已连接的安装版扩展领取，不能用临时自动化浏览器替代。

## Migration And Compatibility

- 新数据库直接创建规范化保存表。
- 旧数据库在初始化时事务迁移 `watch_later` 和 `favorites`；迁移可重复执行且不产生重复 membership。
- `content_cache` / `recommendations` 的遗留身份同步迁移到 `item_key`，并用跨平台同裸 ID 的夹具证明不再冲突。
- 旧 B 站 API 请求 `{bvid}` 继续工作；响应增加字段保持向后兼容。
- 旧前端不知道同步字段时仍可使用本地收藏与稍后看。
- 迁移完成前后，本地保存内容仍参与 candidate suppression、封面缓存保留和保存列表查询。
- 同一内容同时属于收藏和稍后看是合法状态。若某平台两种意图最终都解析为同一 native favorite，第二次同步按 `already_synced` 成功，不删除任何本地 membership。

## Automated Testing

后端测试覆盖：

- 七个平台乘以两种用户意图的完整路由矩阵。
- 平台 alias、稳定 ID、URL fallback 和跨平台同 ID 不冲突。
- 新建数据库、旧表迁移、重复迁移和 B 站兼容 API。
- 本地先写、自动同步开关默认关闭、手动同步忽略开关。
- 单项、混合平台批量、部分失败、重复同步和 `already_synced`。
- `login_required`、`unsupported`、`rate_limited`、收藏夹创建失败和 extension disconnected。
- 同一 item 同时属于两个本地列表且映射到同一平台目标。
- 删除本地记录不会调用平台删除。
- `/api/config` round-trip 和旧配置默认值。

扩展测试覆盖：

- 每个平台任务 dispatcher 的领取、kick、timeout、鉴权回传和结构化错误。
- 真实内容 ID 定位、已收藏状态识别、命名收藏夹创建 / 选择和重复保存。
- popup 单项保存、全部稍后看、本地失败回滚、同步中和平台失败提示。
- 设置开关默认关闭、确认文案和保存回读。
- 收藏页 / 稍后看页的单项与批量同步状态。

Desktop / mobile / CLI 测试覆盖：

- 设置字段和值一致。
- 保存页的真实平台目标、状态、计数和错误展示一致。
- 手动同步按钮在自动开关关闭时仍工作。
- extension-backed 平台在未连接时提示一致。

## Real E2E Verification

真实验证使用安装了当前扩展且已有登录态的浏览器，不用 CDP/MCP 临时浏览器替代。每个平台至少验证：

1. 关闭自动同步，推荐卡保存只改变本地状态。
2. 从本地列表手动同步，平台账号出现真实记录。
3. 开启自动同步，推荐卡保存触发即时平台写入。
4. 收藏和稍后看分别命中平台映射表中的目标。
5. 已存在内容重复同步返回成功且不制造异常重复。
6. 登出后返回 `login_required`，本地记录保留。
7. 混合平台批量同步展示正确成功 / 失败数量。
8. 平台支持命名容器时创建或复用 `OpenBiliClaw`；不支持时显示真实默认目标。

真实 E2E 会修改账号，必须获得用户当次授权或使用测试账号。验证输出记录命令、task result 和数据库状态，不记录任何凭证。

## Documentation And Release Scope

实现时按仓库强制清单同步：

- `docs/changelog.md`
- `docs/modules/config.md`
- `docs/modules/extension.md`
- `docs/modules/api-auth.md`、`docs/modules/integrations.md`、`docs/modules/storage.md`、`docs/modules/runtime.md`、`docs/modules/recommendation.md`
- `docs/modules/cli.md`
- `docs/architecture.md`
- `docs/spec.md` §3 系统架构图
- `README.md` / `README_EN.md` 顶部架构图和用户能力说明
- `config.example.toml`
- 插件、桌面和移动设置文案

该功能改变跨模块数据流、配置、API、数据库和外部平台集成，不能只更新 issue 或插件代码。发布前还需完成后端全量测试、MyPy、Ruff、扩展 test/typecheck/build、真实登录态平台 E2E 和版本 / release 资产对齐。
