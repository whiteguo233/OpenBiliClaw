# 平台来源接入契约

## 概述

`src/openbiliclaw/api/source_auth/` 回答一个问题：**每个平台来源的凭据现在能不能用，以及这个结论有多可信。**

它存在的原因是旧实现把四个互相独立的问题挤进了一个 `state` 字符串。结果是多个平台在设置页并排显示同一个「凭据已就绪」，而背后的证据强度天差地别——B 站只做本地 Cookie 结构检查，小红书、知乎与 Linux.do 使用浏览器心跳，Reddit 读取本地凭据文件，X 既有被动健康记录也能显式只读探测，Bangumi、GitHub 与 V2EX 则匿名可用但支持可选令牌。用户无从分辨「真的能用」和「只是填了个值」，这份契约因此把凭据存在、验证结论与证据强度拆开表达。

完整诊断与设计见 [`docs/plans/2026-07-18-source-auth-contract-spec.md`](../plans/2026-07-18-source-auth-contract-spec.md)。

## 已实现功能

| 能力 | 说明 | 状态 |
| --- | --- | --- |
| 正交契约 `SourceAuthContract` | 四个维度互不覆盖：要不要凭据 / 凭据在不在 / 验证结论 / **结论有多硬** | ✅ |
| 每平台 provider | `providers.py` 的纯函数 provider 取代旧 if/elif 聚合器 | ✅ |
| Bangumi / GitHub / Linux.do / V2EX 接入契约 | 四者公开能力均可匿名；Bangumi / GitHub / V2EX 支持可选令牌，Linux.do 个人能力使用浏览器心跳与任务历史 | ✅ 见下方各来源说明 |
| 显式验证动作 | `POST /api/sources/{slug}/verify`，所有已注册来源共享三态结果 | ✅ |
| 并发安全的本地状态读取 | X 健康表首建单飞，读写使用短生命周期 SQLite connection；状态轮询不共享 connection | ✅ |
| 统一凭据写入 | `POST /api/sources/{slug}/credential`，写入即校验，7 条老端点转发 | ✅ |
| 表单描述符 | `forms.py` 下发每平台表单形态，三端零 per-platform 分支 | ✅ |
| 三端共享渲染 | `web/shared/source-status.js`，desktop / popup / setup 引导页共用 | ✅ |
| 证据强度可见 | 状态标签与色调由 `auth` 驱动；`verify_method` 渲染成独立徽章，非纯色编码 | ✅ |
| 抖音活体探针 | `sources/douyin_login_probe.py`，修复其永久误报 | ✅ |
| 移动 Web 接入 | 尚未提供来源设置入口 | ⏳ Wave C |
| B 站凭据迁出 config.toml | 目前仍以明文存于 config | ⏳ Wave C |

## 公开 API

### 契约字段

```python
class SourceAuthContract(BaseModel):
    auth_required: bool                # 这个源要不要凭据（YouTube=False）
    credential: "none" | "present" | "invalid"
    credential_origin: "config" | "env" | "data_file" | "extension" | "external_cli" | "none"
    verification: "verified" | "failed" | "stale" | "unverified" | "rate_limited" | "blocked"
    verify_method: ...                 # 见下表，这是核心字段
    verify_ttl_seconds: int | None
    verified_at: str                   # ISO-8601，与 api/models.py 其余时间戳一致用 str
    can_verify_now: bool
    detail: str
```

`verify_method` 按证据强度排序，**它的作用是让绿灯诚实**：

| 取值 | 含义 | 当前平台 |
| --- | --- | --- |
| `live_probe` | 当场出网问平台 | bilibili、douyin、twitter、bangumi、github、v2ex（后三者配置了个人令牌时） |
| `passive_health` | 由真实流量的错误反推 | 暂无当前平台（保留能力） |
| `browser_heartbeat` | 插件报告登录 cookie 存在 | xiaohongshu、zhihu、linuxdo（仅 `_t` 布尔存在性） |
| `local_file` | 只读了本地凭据文件 | reddit |
| `task_history` | 由历史任务结果反推 | zhihu、linuxdo（无心跳时回落） |
| `none` | 无验证能力，或不需要 | youtube、tiktok（未配置 Cookie 时）、bangumi / github / v2ex（未配置令牌时）、linuxdo（无心跳且无任务历史时） |

### 三端如何渲染这份契约

契约字段若不落到像素上，整个重构对用户不可见。`describeAccess()` 因此**只读 `auth`**，legacy `state` 仅作老后端兜底。判定先看 `hasVerifiableCredential()`：只有 `auth_required=false` 且没有可验证凭据或个人能力时才直接显示「无需登录」；若匿名源另有 `live_probe` / `browser_heartbeat` / `task_history` 等可选身份能力，则继续按 `credential`（`none` / `invalid`）和 `verification` 渲染。凭据维度先于结论维度，是因为两者正交、可能互相矛盾，此时宁可少报也不点亮一盏兜不住的绿灯。

| verification | 标签 | tone |
| --- | --- | --- |
| `verified` | 已验证 | ready |
| `failed` | 登录已失效 | danger |
| `stale` | 验证已过期 | warning |
| `unverified` | 待验证 | pending |
| `rate_limited` | 频率受限 | pending |
| `blocked` | 接入受阻 | danger |

`auth_required=false` 且没有可验证凭据或个人能力时单独落在**无需登录**档（public 灰），既非已验证也非待验证。匿名可用但带 optional credential / identity 的来源不能走这个短路：Bangumi / GitHub / V2EX 配置令牌后显示 `live_probe` 证据；Linux.do 把 `/session/current.json` 个人任务终态作为最强证据，缺少任务证据时使用 `_t` 布尔 `browser_heartbeat`，两者都没有时才回落公开 discovery。共享 UI 因而不会把匿名发现成功伪装成个人登录成功。

证据强度是**独立于结论**的第二维度，同时用三种方式编码，其中没有一种是颜色（色觉障碍用户读不到颜色）：

| verify_method | 字形 | 文案 | rank | 边框 |
| --- | --- | --- | --- | --- |
| `live_probe` | ◆ | 联网验证 | `direct` | 实线 |
| `passive_health` | ◆ | 请求反馈 | `direct` | 实线 |
| `browser_heartbeat` | ◇ | 插件心跳 | `indirect` | 虚线 |
| `local_file` | ◇ | 本地文件 | `indirect` | 虚线 |
| `task_history` | ◇ | 历史任务 | `indirect` | 虚线 |
| `none` | — | 无验证能力 | `unable` | 点线 |
| 本版本不认识的取值 | ◇ | 验证方式未知 | `unknown` | 虚线 |

未知取值刻意渲染成**弱**而非强：猜一个没见过的方式「很硬」正是这套契约要消除的过度声称。

文案命名的是**能力**而非结果——B 站在首次探针跑之前 `verify_method` 就已是 `live_probe`。结论到底跑没跑由后缀承担：`verified_at` 有值渲染成「3 分钟前」，为空则渲染「尚未验证」。把这两件事分开，才不会让一项能力被读成一个结果。

于是 spec Goal 段那个场景终于可分辨——B 站与 Reddit 的 legacy `state` 同为 `ready`、tone 同为绿色、标签同为「已验证」，但徽章分别是 `◆ 联网验证 · 刚刚` 与 `◇ 本地文件 · 2 天前`。

**`verified_at` 一律带时区，在后端归一。** 曾经有两种线格式：多数 provider 发 `datetime.now(UTC).isoformat()`（带 `+00:00`），而三处从 SQLite 读回时间戳的（X 的 `x_source_health`、知乎与 Reddit 的 `task_history`）发的是 `CURRENT_TIMESTAMP`——是 UTC 却不带时区标记。`Date.parse` 会把后者当本地时间，UTC+8 用户看到的**新鲜**结论会凭空老 8 小时，方向还正好反了：让最硬的证据显得最陈旧。现由 `SourceAuthContract` 的 `verified_at` field validator 统一补齐时区，**放在契约边界而不是各 provider 里**——这是所有契约的唯一必经之处，新 provider 无从绕过，移动 Web 与 CLI 也不必各自再防一次（CLAUDE.md pitfall #5：共享逻辑放后端）。无法解析的字符串**原样透传而非清空**：`""` 会被读成「从未验证」，还会触发 `check_legacy_consistency` 的新鲜度断言。前端 `normalizeTimestamp()` 保留为对老后端的防御。

**活体探针有两个不同期限。** `PROBE_OK_TTL_SECONDS=60` 只回答“下一次显式验证能否复用这次结果”：超过 60 秒，再点测试连接必须重新访问平台，不能拿旧结论授权凭据写入。用户可见的成功证据则由 `_VERIFIED_FRESH_SECONDS=6h` 控制；一次真实成功不会在 61 秒时突然变成“验证已过期”，6 小时后才转 `stale`。失败仍使用 10 秒短窗口，让用户修复凭据后能尽快复验。provider 的实际判定与对外 `verify_ttl_seconds` 共用这套 6 小时可见窗口，避免“接口宣称 6 小时、页面 60 秒就过期”的分叉。

三端出口不同，数据同源：桌面页给证据一个独立徽章（`.source-evidence-badge[data-rank]`）；侧边栏每源只有一行，用 `access.line` 把证据括号内联（`已验证（◆ 联网验证 · 3 分钟前）：…`）；setup 引导页在 B 站步骤打印同一份标签与证据。

### 端点

| 端点 | 说明 |
| --- | --- |
| `GET /api/sources/status` | 每平台 `SourceStatusItem`，含 `auth` 子对象；**纯读，绝不出网** |
| `GET /api/sources/credentials` | 只写秘密的掩码状态 + `form` 表单描述符；`reveal_keys=true` 兼容接受但不导出原值 |
| `POST /api/sources/{slug}/verify` | 显式验证，返回契约 + `outcome` / `replayed` / `retry_after_seconds` |
| `POST /api/sources/{slug}/credential` | 统一写入：结构校验 → 活体校验 → 落盘 → 广播 → 返回重算契约 |

GitHub 与 Bangumi 的 optional token 是明确的 config-only 兼容路径：二者在
`CREDENTIAL_SPECS` 中登记 `kinds=()`，统一 `/credential` 不接收 token；设置页与
guided init 通过 `PUT /api/config` 的 write-only 字段保存并复用同一只读活体验证门。
GitHub 还可只从固定的 `OPENBILICLAW_GITHUB_TOKEN` 读取，不接受可改名的环境变量、
`GITHUB_TOKEN` 或 `GH_TOKEN`。

8 条老写入端点（`/api/bilibili/cookie`、`/api/sources/{dy,x,reddit}/cookie`、`/api/sources/xhs/tokens`、`/api/sources/{xhs,zhihu,linuxdo}/login-state`）保留为 `deprecated=True` 的内部转发，响应结构**逐字段冻结（值，不只是键集）**——它们有浏览器扩展在调用，而一个键还在、值被掏空的响应对只比对键集的测试是隐形的。`PUT /api/config` 的四处凭据写入同样委托统一校验门。
`state / logged_in` 是给旧客户端和 Agent 宿主的兼容视图，但仍由各平台 provider 负责，不能与
同一响应里的 `auth` 自相矛盾。抖音读取最近一次匹配当前凭据的活体探针：`verified` 同步映射为
`ready / true`，`stale` 映射为 `stale / false`，其余结论映射为 `unverified / false`。状态端点
仍只读 probe cache、绝不因轮询出网。

**凭据读取是状态查询，不是秘密导出。** `GET /api/sources/credentials` 的 `available`、掩码预览、`summary` 与非敏感 Cookie 名称用于回答“是否已保存/由哪里管理”；秘密原值永不返回。历史 `reveal_keys` query 保留为 no-op，`form.actions` 不再包含 `copy`，桌面页面也不渲染复制按钮。新值只能走统一 credential 写入或配置 PUT；空值与掩码回显不会覆盖现有值。

**首页问题分类与设置卡共用同一张表。** `describeSourceIssue()` 只把已启用来源的缺凭据、不完整、过期、失败、受阻、限流和未知契约列为 actionable issue，并原样携带后端 `detail`；`unverified`、`syncing`、无需登录与已验证都不是故障。桌面首页因此能覆盖所有已注册平台并点名具体来源，而不会把每个平台都冒充为 `AccountSyncService` 的账号同步阶段。

**任何写入面都没有降低校验强度的开关。** `POST /api/sources/{slug}/credential` 的 `validate_live` 已删——全仓零调用方（扩展、三端前端、CLI 都不发），等于给任何能连上 localhost 的东西一个关掉端点核心承诺的官方途径，不换来任何好处。老端点 `POST /api/bilibili/cookie` 的 `validate_with_bilibili` **仍接受但不再生效**：只删新端点而把隔壁一模一样的开关留着，等于那次删除只是装饰——「装机扩展总是发 true」描述的是扩展，不是所有能连上这个端口的东西；实测传 `false` 会让一份结构完整但已失效的 cookie 在探针零调用的情况下落盘。字段保留在协议上是因为装机扩展每次同步 cookie 都会发它，直接拒绝该键会 422 掉它们的同步；**「接受这个字段」与「这个字段能降低校验」是两件事**。`validate_credential()` 也随之删掉了 `live` 参数——能活体校验的平台一律活体校验，没有"少查一点"的入参。

**`PUT /api/config` 的外部凭据写入推迟到保存路径之后。** 抖音 / X / Reddit 的凭据落在 config.toml 之外（`data/*.json`、rdt-cli 凭据库），既没有快照也没有回滚。这些分支过去在**字段解析处**就直接写盘，而 `[network]` 校验、配置阻断性校验、保存锁都在几百行之后——于是一个同时带着有效抖音 cookie 与非法 `network.mode=custom, proxy=""` 的请求会返回 400「配置校验失败，未写入」,而 cookie 早已被覆盖。现在四个平台的写入收集成延迟闭包,在 `_CONFIG_SAVE_LOCK` 内、`save_config()` 成功之后统一执行:所有 400/409 出口都在其之前,并发 PUT 也不再可能拼出「config 来自甲请求、凭据来自乙请求」的状态。**改的是顺序不是位置**——持久化仍留在 handler 里,因为它正处在 config.toml 事务中,不能让共享写入器把待写的编辑冲掉。

## 设计决策

**旧 `state` 是 provider-owned compatibility，不是全局推导。** 原计划写一个 `derive_legacy_state(contract)`，实施时证明不可能：同样的正交字段在不同平台具有不同历史兼容语义。各 provider 因而继续拥有自己的映射，`legacy.py` 的 `check_legacy_consistency()` 只断言两套视图不矛盾。provider 获得更强证据时可以在旧词汇内同步升级；抖音活体探针成功后若仍固定输出 `unverified / false`，会让同一响应的外层与 `auth.verification=verified` 直接冲突，因此现已映射为 `ready / true`。

**状态端点绝不出网，由作用域强制。** `SourceAuthContext` 只持有 config 与 database，**拿不到 HTTP client**。PC Web 收到凭据/登录态同步 runtime 事件（包括 Linux.do 的布尔心跳）后会立即重读该端点；文档可见时仍每 30 秒轮询一次，作为事件遗漏或 WebSocket 重连空窗的兜底，并同时刷新首页警示与来源卡片。若状态端点自己探测，一个空闲标签页就会周期性访问外部平台、永不停止——那是自造风控。活体探测只发生在显式的 verify 动作里，状态端点通过 `probe_cache.LiveProbeCache.peek()` 读取上次结论（零 I/O）。

**verify 动作按固定动作表分派，不按 `verify_method`。** 两者不同：`verify_method` 描述「当前这个结论怎么来的」，随状态变化（知乎无心跳时回落 `task_history`）；而一次点击要做的事是平台的固定属性（知乎永远是「请插件重新上报」）。按前者分派会让知乎**在最需要验证时反而没有可执行动作**，还会凭空造出「重跑历史」这种不存在的操作。

**`outcome` 与 `verification` 必须分离。** 前者答「这次点击验证到了什么」，后者答「我们现在相信什么」。插件未连接时，一小时前的心跳仍让 `verification=verified`，但这次点击什么都没验证到 → `outcome=indeterminate`。合并两者会渲染出绿色「已验证」配「插件未连接」。

**三态而非两态。** 探测超时、插件没回、平台限流、YouTube 无需登录，全部是 `indeterminate`。把「判定不了」显示成「凭据失效」，会让用户去删一份好好的 cookie。共享模块的 `neutral` 色调特意**不用灰色**，以免与「仍在探测」混淆。

**`detail` 必须跟着 `verification` 走。** 一张卡片上有三句话在说同一件事：标签（来自 `verification`）、证据徽章（来自 `verify_method` + `verified_at`）、正文（`detail`）。抖音的 `detail` 曾是一个写死的常量「Cookie 已同步，需在实际任务中验证。」——那是 D11 之前它确实无法验证的年代留下的文案，探针接上后没人回头改。于是三端切到正交契约后，真机截图里抖音那张卡片同时写着「接入：已验证」「◆ 联网验证 · 刚刚」和「需在实际任务中验证」。这条**所有测试都没抓到**，因为 `detail` 只在「尚无结论」这一个状态下被冻结，而那恰好是老文案仍然正确的状态。现在 B 站与抖音的 `detail` 都按 `verification` 查表；`unverified` 一档保留原字符串,所以冻结用例逐字节不变,新文案只出现在旧实现根本到不了的状态里。**凭据维度的措辞不随结论变**——「Cookie 缺少登录字段」讲的是结构,探针结论如何都不改变它。

**去抖条目随凭据变更失效。** 每平台 10 秒去抖是为了防连点自造风控，但它按**平台**存结果。修复路径恰好会撞上：验一份死 cookie（窗口以 `failed` 武装）→ 粘贴一份能用的 → 10 秒内再点「测试连接」→ 原样回放那条旧的失败。用户读到的是「修了也没用」，下一步多半是把那份真正能用的 cookie 删掉。凭据一旦真正落盘（两条写入路径都会），`note_credential_changed()` 立即清掉该平台的去抖条目。同理，`asyncio.CancelledError` 继承自 `BaseException`，`except Exception` 抓不到——前端 fetch 被取消或上层超时会让 in-flight 标记残留到 60 秒上限，期间每次点击都只回「验证正在进行中」，而那次验证早已停止。

**无法校验的绝不伪造。** 小红书、知乎与 Linux.do 后端只存一个 bool、零字节 cookie，其写入显式返回 `checked="none"` 加 `unverified_reason`，而不是假装校验过。

**活体缓存按凭据判定，不按平台。** 写入门会复用 60 秒内的**正面**结论（抖音 `msToken` 频繁轮换，插件每次启动都重发整个 jar，每次都探测就是自造风控）。但复用只对**同一份凭据**成立：只按平台取缓存时，旧 cookie 的成功结论会替另一份结构完整却已失效的 cookie 背书，于是无效凭据一个网络请求都不发就落了盘——「无效凭据绝不落盘」在用户唯一看不见的那种情况下失效。`ProbeVerdict.credential_fingerprint` 存的是该平台**登录态字段**的 SHA-256（B 站 `SESSDATA`/`bili_jct`/`DedeUserID`，抖音 `sessionid`/`sessionid_ss`/`sid_tt`），字段名直接取自 `CREDENTIAL_SPECS` 的校验门，所以「什么算同一份凭据」与「校验门要求什么」不可能漂移；`msToken` 不在其中，轮换因此仍然命中缓存。写入门用 `peek_matching()`（**严格**：指纹不符或缺失一律重探，因为猜错的代价是死凭据落盘）；状态端点用 `contradicts()`（**宽松**：只有明确不符才丢弃，缺失指纹仍显示；展示仍受 6 小时可见证据窗口约束，而凭据写入绝不会借用这个长窗口）。命中缓存的结论**不重新记录**，否则每次插件重发都会顺延自己的有效期，一份凭据可以永远「刚刚验证过」而实际从未复验。

**X 的成功属于某一份凭据，不属于这个平台。** 只记「成功过」不记「谁成功的」，换 cookie 就会继承上一份的结论——实测新 cookie 直接拿到旧 cookie 的 `verified`**连时间戳都一字未变**。这与上一条按平台取缓存是同一个错误，只是换了个 store。`last_success_credential` 记下产生该成功的凭据指纹（由 producer 在解析 cookie、构造 `XClient` 的同一处绑定，所以「记录成功的那份凭据」就是「发出请求的那份凭据」），读取时与当前 cookie 比对，不符即非证据。**不挂在写入路径上而是比对身份**：cookie 也可能经环境变量或直接改 data file 变更，那些路径一个钩子都不经过；`clear_relogin_block()` 更救不了——健康行本就是 `ok` 时它返回 `False`，什么都不清。build 期绑定也是安全方向：若 cookie 变了而 producer 未重建，指纹仍跟着**真正在发请求**的那份凭据走,新 cookie 显示 `unverified` 而非冒领他人战绩。

**X 的 `ok` 不等于验证通过。** `x_source_health` 的行是以 `state='ok'` 为**默认值**建出来的,所以「从未发过任何请求」与「上次请求成功」在 `state` 上完全同形。照 `ok` 直接映射 `verified`,意味着全新数据库里第一次写入的 X cookie——哪怕它几个月前就过期了——会立刻宣称 `verification=verified`。现新增 `last_success_at` 列(只由 `record_success` 写),没有它就报 `unverified`；此时设置页的 `live_probe` 可以通过只读 `fetch_me()` 主动补上成功证据。`clear_relogin_block()` 会**清空**该列：它是凭新 cookie 给出的乐观解封,不是用新 cookie 拿到的结果,留着旧成功等于让新凭据继承别人的战绩。迁移**不回填**——老行里没有任何信号能区分这两种情况,猜一个就是把同一个伪造推迟一次迁移;代价是升级后 X 显示 `unverified` 直到下一轮 discovery 或显式测试连接成功。

**X 的显式验证是只读探针。** `POST /api/sources/twitter/verify` 复用统一 `live_probe` 框架，调用 `XClient.probe()` → `twitter-cli.fetch_me()`；成功或明确 401 会写回与 discovery 共用的 `XSourceHealthStore`，403 / 429 与其它传输异常保持 `blocked` / `rate_limited` / 待判定语义，不把无法判定写成 Cookie 失效。保存 Cookie 本身仍只做 `auth_token` / `ct0` 结构校验，避免每次扩展同步都主动出网。

**X 健康表不能在状态请求的共享 connection 上做任何工作。** `/api/sources/status` 是同步 handler，会被 FastAPI 线程池并发执行；`check_same_thread=False` 只允许 connection 跨线程使用，不代表同一个 connection 可以同时 `CREATE/PRAGMA/SELECT`。真实 30 并发请求曾有 3 次返回 500（`sqlite3.Connection returned NULL`）。`XSourceHealthStore` 现对每个 `Database` 实例只单飞执行一次 schema 初始化，且初始化、读取、成功/失败写入、人工冷却覆盖均使用 `Database.open_connection()` 的短连接；`record_error()` 的计数读取与更新位于同一 `BEGIN IMMEDIATE` 事务，既避开共享 connection，也不丢连续 429 计数。

## Linux.do 的接入

Linux.do 的形态是“公开 discovery 无需登录 + 个人 bootstrap/login-required”。legacy `auth_required` 仍为 `false`，但共享契约新增 `capabilities` 矩阵：discover 永远 `anonymous/ready`，profile/bootstrap/incremental 必须是 `login-required/ready` 才能作为 guided-init 个人信号来源。这样 `_t` 缺失、心跳陈旧或扩展离线不会误伤公开 search / hot / feed / creator / related，也不会让 Linux.do-only 初始化在未登录时先排一个长任务才失败。

扩展读取 `linux.do` Cookie 列表后只计算 `_t` 是否存在且非空，并通过 deprecated-compatible `/api/sources/linuxdo/login-state` 或统一 credential writer 保存 `logged_in: bool`。后端不会收到 `_t` 值，也没有可用于 direct probe 的 Linux.do cookie：

- 新鲜的 `true` 心跳：`credential=present`、`credential_origin=extension`、`verification=unverified`、`verify_method=browser_heartbeat`；它只表示可尝试个人任务，不冒充 `/session/current.json` 身份验证。
- 陈旧的 `true` 心跳：仍是 `credential=present`，但 `verification=stale`。
- 新鲜 `false`：`credential=none`、`verification=failed`，detail 明确只说个人信号暂不可用。
- 从未收到心跳且没有任务历史：`credential=none`、`verification=unverified`、`verify_method=none`，共享 UI 显示「无需登录」，公开发现仍可用。

实时 `/session/current.json` 任务证据强于 Cookie 心跳：72 小时内的个人任务 `login_required` 不会被后到的 `_t`-exists 覆盖，必须由后续个人任务成功恢复；较新的个人成功也会优先于旧心跳。任务证据超过 72 小时后只保留 stale/history 提示，不再永久准入或永久拦截；此时新的浏览器心跳可重新发起一次由 `/session/current.json` 把关的尝试。没有更强的个人终态时才使用心跳。两者都没有时，provider 从 `linuxdo_tasks` 读取最近 bootstrap 与 discovery 终态：新鲜的成功 / 空结果 / 部分成功个人 bootstrap 映射为 `present + verified + task_history`；公开 discovery 成功只证明公开链路可用，仍是 `none + unverified + task_history`；其它失败或进行中任务保守表达为 `none + unverified`。这些都不改变 legacy `auth_required=false`，个人能力 admission 始终看 `capabilities`。

前端可见状态严格跟随 optional-auth 逻辑：新鲜 `true` 显示「已验证（◇ 插件心跳）」；陈旧 `true` 显示「验证已过期」；已收到 `false` 时因仍有 heartbeat 能力而显示「公开发现可用」；无心跳但有任务历史时显示对应的间接证据；两者皆无时才显示基础「无需登录」。这些 badge 只描述个人增强能力或最近公开链路结果，不得反向阻断公开 discovery。

固定 verify 动作是 `browser_heartbeat`：后端经 runtime-stream 发布 `linuxdo_login_state_sync_requested`，在线扩展重新判断 `_t` 并上报 bool；插件未连接、规定时间内没回报或事件通道不可用时 outcome 为 `indeterminate`，绝不把“浏览器没回答”误写成“用户已退出”。即使 bool 为 true，个人任务执行时仍必须由同源 `/session/current.json` 返回 `current_user.username` 正面确认身份；心跳只是能力提示，不是分页授权。

这条契约与 Linux.do 任务的隐私边界一致：站点请求只在真实 `linux.do` tab 内以 GET 执行，Cookie、CSRF 字段和原始 JSON/HTML 响应都不上传。详见 [Linux.do 来源文档](linuxdo.md)。

## Bangumi 的接入

Bangumi 在 v0.3.174 作为第 8 个平台合入，起初走 `auth: null` 过渡态。现在它有了
真契约（`providers.py::auth_bangumi`），但它打破了 `auth_required` 布尔的隐含假设，
解法值得记下来。

**它是第三种形态：匿名可用 + 可选可验证凭据。** 既有平台里，YouTube 是
`auth_required=false` + `verify_method=none`——没东西可验；其余六个 `auth_required=true`。
Bangumi 两者都不是：公开收藏 / 排行**匿名即可发现**（所以从「能不能用这个源」看它
`auth_required=false`），**但给了个人令牌就能验证令牌**（`GET /v0/me` 有效令牌返回账号、
无效令牌返回 `unauthorized`）。

**契约怎么表达这个组合（本次的选择）：**

- `auth_required` **恒为 `False`**——你从不「需要」登录 Bangumi。无令牌时它就是
  YouTube 的形状（`credential=none` / `verify_method=none`），前端渲染「无需登录」、
  零告警，满足「匿名可读是正常状态」这条硬约束。
- 配了令牌时 `credential=present`、`verify_method=live_probe`、`can_verify_now=true`，
  `verification` 读共享探针缓存的 `/v0/me` 结论：从未验 → `unverified`，令牌被拒
  （`unauthorized`）→ `failed`，验证通过 → `verified`+`verified_at`，网络/超时/限流
  → `unverified`（indeterminate，绝不 `failed`）。出网走 `outbound_httpx_kwargs()` 代理策略。
- **控制实验（§0.1 / I3）**：2026-07-19 实测 `/v0/me`——真实令牌→`username='215952'`，
  伪造令牌→`unauthorized`，无令牌→`unauthorized`。判据是**两组之间有差异**，不是单组
  看着正常，所以 `live_probe` 名副其实。复现见
  `tests/test_source_auth_contract.py::test_bangumi_verify_with_a_valid_token_is_verified`
  等四个用例。

**可选凭据的前端表达。** 共享 renderer 现在用 `hasVerifiableCredential()` 区分纯匿名源与
可选身份源：Bangumi 无令牌时仍显示「无需登录」且无徽章；配置令牌后按
`verification` 显示已验证 / 待验证 / 失败，并展示 `◆ 联网验证` 证据。discovery 运行时的
`token_state="rejected"` 仍保留为来源健康维度，与 auth badge 相互补充。
**可选凭据现在有常驻证据。** 共享 renderer 不能只看到 `auth_required=false` 就提前返回：
无令牌时 Bangumi 仍显示「无需登录」；有令牌时，`hasVerifiableCredential()` 让
`describeAuthVerdict()` / `describeEvidence()` 展示 `verified/failed/unverified` 和 ◆ 联网验证，
同时 discovery 的 `token_state="rejected"` 保留运行期权威否定。对应状态转换由
`extension/tests/source-status.test.ts` 锁定，新增 optional-credential 来源必须复用这套语义，
不得恢复「匿名可用就隐藏已保存凭据」的旧短路。

**legacy 与两条额外维度。** `legacy_state` 恒为 `no_auth`（保持一致性检查严格；
discovery 健康串落到 `detail`，`token_state` 单独成轴）。Bangumi 的 `SourceStatusItem`
仍由 `api/app.py::_bangumi_status_item()` 装配，因为它带两条 uniform item 装不下的维度：
一条 discovery 健康 `detail`（`尚未运行` / 退避冷却 / 运行结果），和 `token_state`
（`ok` / `rejected` / `""`）。`state` 不再吞 `enabled`（D12 的旧毛病），停用只体现在
独立的 `enabled` 字段上。

**一致性检查放宽了一处。** `legacy.py` 原规则「`auth_required=false` 不得带
非-`none` 的 `verify_method`」是给 YouTube 写的（无凭据可验）。Bangumi 的可选令牌是
合法反例，故规则收紧成「`auth_required=false` **且 `credential='none'`** 才禁止 live
方法」——有凭据时验证它是诚实，不是过度声称；YouTube 的过度声称仍被拦（见
`test_optional_credential_may_carry_a_live_method_but_none_may_not`）。

**令牌写入仍走 config。** Bangumi 令牌由 config / init 表单写入
（`sources.bangumi.access_token`，保存时用 `/v0/me` 校验），不经统一 `/credential`
端点，所以它的 `CredentialSpec` 是 `kinds=()` + `form_kind='none'`——表单只给「测试连接」
和「去获取令牌」链接，不给会写到空处的粘贴框。探针缓存要区分不同令牌，故 `CredentialSpec`
新增 `opaque_credential=True`，指纹覆盖整串令牌而非 cookie 字段名。

## GitHub 的接入

GitHub 与 Bangumi 一样属于「匿名公开能力 + 可选可验证凭据」，但首版范围更窄：只读取
公开 repository。无 PAT 时，repository discovery 与显式公开用户名的 starred collection
都可用；provider 返回 `auth_required=false`、`credential=none`、`verify_method=none`。
提供 PAT 后，固定验证动作调用只读 `GET /user`，成功才得到 `verified` token identity；401
记录明确失败，限流或网络错误保持 indeterminate，不把传输故障写成令牌失效。

账号范围与所有权证据分开：`GET /user` 的 numeric user id 是 PAT 的 verified identity；
`GET /users/{username}` 只能确认公开账号存在，状态是 accepted scope，不证明当前用户拥有它。
同时给出 PAT 和用户名时，后端分别取两边 durable numeric id；不一致就返回稳定的
`github_identity_mismatch` 并停止 starred bootstrap，匿名 public discovery 继续工作。客户端
只提交用户名 / token 草稿并展示结论，不复制这套 admission。

凭据解析顺序固定为 `OPENBILICLAW_GITHUB_TOKEN` → `[sources.github].access_token`。
`sources.github.token_env` 只为配置/API shape 兼容保留，解析器不会采用任意改名，更不会借用
开发工具常见的 `GITHUB_TOKEN` / `GH_TOKEN`。PAT 即使拥有私有仓库权限也不会扩大内容范围：
服务端查询固定添加 `is:public`，normalizer 再拒绝 `private=true` 行。

正式 discovery 若收到 PAT 401，会在本地 GitHub discovery state 写入短指纹而非令牌正文。
provider 只在标记与当前 PAT 指纹一致时把凭据 verdict 设为 failed，并让 status / init-status
的 profile 与 bootstrap 轴显示 unavailable；anonymous discovery 轴仍保持独立。令牌轮换或清除后
旧指纹不匹配，状态会恢复到新凭据对应的未验证 / 匿名能力，不需要手工删除数据库行。

GitHub PAT 不经统一 `/credential` 写入；其 `CredentialSpec` 使用 `kinds=()`、
`opaque_credential=true` 与 `form_kind="none"`，防止三端生成一个写不到任何地方的通用粘贴框。
桌面 / popup 的 GitHub 来源卡仍可通过配置保存语义维护 token：字段省略或 masked 表示保留，
显式空字符串表示清除，明文从不出现在配置 GET、状态响应或日志中。完整来源边界见
[GitHub 来源文档](github.md)。

## V2EX 的接入

V2EX 沿用 Bangumi 的「匿名可用 + 可选 PAT」形态，但 PAT 字段来自
`[sources.v2ex].access_token` 或 `token_env`，不是浏览器 Cookie。无 PAT 时 provider 返回
`auth_required=false`、`credential=none`、`verify_method=none`、`legacy_state=no_auth`；有 PAT
时返回 `credential=present`、`verify_method=live_probe`，验证请求调用只读
`GET /api/v2/member`。PAT 不会通过配置 GET 接口返回明文，401/403 只表示令牌失效并允许公开
discovery 继续匿名运行。

V2EX 的浏览器登录态是独立于 PAT 的能力：扩展本地只检查 A2 是否存在并向后端发送
`logged_in` 布尔心跳，页面可见用户名以 `observed` 身份证据上报；后端不接收 Cookie 值。
bootstrap 任务通过四个只读 scope 读取渲染后的公开行，任务结果状态和 PAT 状态在 source-auth
中分开显示，不把其中一项推断成另一项。PAT 只有只读 `/api/v2/member` 正面返回账号后才形成
`verified` identity；后端只保存 username 与当前 PAT 的单向 credential fingerprint，token
改变后旧 verified claim 自动失配，不保存 PAT 副本。PAT identity 最多信任 6 小时；匹配当前
fingerprint 的明确 401/403 会清除旧声明，且内存中的明确失败优先于旧持久成功，防止状态回退成
假 `verified`。传输失败不清除声明，也不把网络问题写成令牌失效。

浏览器 observed identity 最多信任 72 小时；扩展上报 `logged_in=false` 时后端同时清除旧 observed
username，避免登出后在新会话里继续沿用旧账号。浏览器心跳与 PAT verdict 始终是两条证据轴。

`sources.v2ex_identity` 在后端按 PAT verified → 浏览器 observed → 当前配置 → 用户 accepted
解析账号。非空 claim 大小写不敏感比较，任意两条指向不同账号即为 `identity_mismatch`；该状态
只暂停账号 bootstrap、增量事件、收藏快照和 Node Affinity，匿名公开 discovery 继续可用。
source status detail 会列出冲突来源，`GET /api/sources/v2ex/identity` 提供同一结构化只读 verdict；
客户端不能通过 identity endpoint 写入 `verified` 证据。设置卡片中的交互式账号切换、切换后的
历史 Soul 投影隔离 / 清理和真实登录浏览器 E2E 仍待补齐。
统一验证动作登记为 `VERIFY_ACTIONS["v2ex"] = "live_probe"`，相关契约回归见
`tests/test_source_auth_contract.py`。

## TikTok 的接入

TikTok 是「匿名可用 + 可选可验证凭据」形态（与 Bangumi 同族）。Web API 后端以 TikTok 自己
签发的**访客身份**读取公开推荐流 / 创作者 / 话题标签列表，访客身份是完整合法的运行模式，
因此 `auth_required` 恒为 `False`、`legacy_state` 恒为 `no_auth`：无 Cookie 时返回与 YouTube
同形的 `credential=none` / `verify_method=none`。

可选登录 Cookie（`[sources.tiktok].cookie_env`，默认 `OPENBILICLAW_TIKTOK_COOKIE`，兜底
`data/tiktok_cookie.json`）解锁关键词搜索（访客身份被上游 gate，2026-10-03 实测）与更高限额。
配置后契约报 `credential=present`（origin `env` / `data_file`）+ `verify_method=live_probe`：
主动探针调用 `/passport/token/beat/web/`（无签名、仅带 cookie 的会话心跳，
`probe_tiktok_login`），`{"message":"success"}` 为 verified，401/403 或显式 error 为 failed，
传输失败与风控空响应一律 indeterminate（绝不误判为失效）。`VERIFY_ACTIONS["tiktok"]` 相应登记为
`live_probe`，动作恒定而方法的读数随有无凭据变化（与 Bangumi 同一 action-vs-method 拆分）。

凭据写入门面同步开通：`CREDENTIAL_SPECS["tiktok"]` 结构门要求 sessionid / sessionid_ss /
sid_tt 至少其一（访客 jar 只有 ttwid / msToken），live gate 复用同一探针，
`POST /api/sources/tiktok/credential` 与 `PUT /api/config` 两个写入面验证强度一致，落盘
`data/tiktok_cookie.json`。

## 新增平台的强制契约

新平台必须在 `providers.py` 填全契约字段、在 `verify.py` 的 `VERIFY_ACTIONS` 登记动作，否则过不了 `tests/test_source_auth_contract.py` 的参数化测试。若动作是 `browser_heartbeat`，还必须同步登记 `_BROWSER_HEARTBEAT_PREFIXES`，提供对应数据库 getter、extension runtime-stream event handler 与来源专属 round-trip test；未知 slug 必须 fail closed，不能落到某个既有平台的 else 分支。

`SourceAuthContract.auth_required` 当前仍是全源布尔，只适合「所有 required capability 同为匿名/登录」或「同一公开路径由 optional credential 增强」的来源。contract 的 `capability_required` 要把必交付流程和可选 account-resolution fallback 分开。若一个来源同时拥有匿名 public discover 与必须登录的 required private bootstrap/incremental，不要任选一个布尔，也不要在 CLI/setup/extension 复制准入判断：先在来源 contract 使用 `capability-specific` + 逐能力枚举，并把共享后端契约、status/setup/init readiness 与集合测试扩展到能表达这组事实；完成前该来源的 auth gate 是 `BLOCKED`。权威执行规则见 [`docs/platform-source-integration.md`](../platform-source-integration.md) §0–§0.1。

**声称某平台「无法验证」之前，必须先做剥离对照实验**（实验组=完整凭据，对照组=剥掉登录 cookie 的游客态，同签名器/UA/时刻），拿不出对照数据不许写进 docstring。这条规则是有代价换来的：抖音的旧 docstring 断言它「没有稳定 nav 端点」，导致整个平台的登录态误报，还成了后续无人去修的理由。详见 [`docs/platform-source-integration.md`](../platform-source-integration.md) §0.1–§0.6。

## 相关文件

- `src/openbiliclaw/api/source_auth/{contract,legacy,providers,probe_cache,verify,write,forms}.py`
- `src/openbiliclaw/sources/douyin_login_probe.py`
- `src/openbiliclaw/web/shared/source-status.js`（三端共享渲染）
- `scripts/source_contract_metrics.py`（CI 量化门）
- `tests/test_source_auth_contract.py`、`tests/test_douyin_login_probe.py`


### TikTok 来源验收补全（2026-10-04）

TikTok 的 passport heartbeat verified 只证明会话有效；搜索可用性须由单独真实搜索验收证明，不由凭据验证状态推导。
