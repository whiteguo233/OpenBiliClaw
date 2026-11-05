# 桌面 Web（Desktop Web UI）

## 手机扫码连接（2026-10-26）

| 已实现能力 | 行为 |
| --- | --- |
| 三种网络 | 「手机版」可选择局域网、系统 / 应用内 Tailscale、公网或自定义地址。 |
| 可访问地址 | 已保存的回环地址不再覆盖局域网探测；仅监听本机时隐藏 LAN / 系统 Tailscale 二维码；没有地址时禁用复制。 |
| 统一链接 | 相机扫码打开 `/m/`，新版原生 App「扫码连接」解析同一链接并保存后端；仅应用内节点带 `obc_connection=tailscale`。 |

公开前端接口：`/shared/mobile-connection.js` 的 `OBCMobileConnection.connectionChoices()` 生成地址选项，`createPanel()` 管理选择、输入、加载与复制状态；插件随包复制同一源码。每次打开重新请求 `GET /api/qr-info`，失败或空响应不复用旧 Wi-Fi 地址。二维码本机编码，短链接用 QR v5，长链接用 v10（最多 271 UTF-8 字节），不调用第三方二维码服务。配置字段不变。使用与协议见[手机扫码连接](../mobile-connection.md)。

## 首屏静态脚本交付（2026-10-23）

```text
包内桌面/共享脚本 → DesktopAssets（固定依赖顺序；最近两个内容版本；预压缩 gzip）
                 → /web/assets/desktop-bundle.js?v=… → /web 一次 defer 请求
共享源码/原资源入口 → 移动端/插件；无需 Node 构建，无安装或配置变化
```

源码 `index.html` 保留独立脚本标签，真实 API 响应将 13 个脚本按原顺序合并成一次 defer 请求；主题恢复等内联代码保持原位。模块 `api/desktop_assets.py` 只读取固定包内资源，按全部脚本、样式和 HTML 输入生成内容版本，并缓存最近两个构建及 gzip 表示。输入不变时复用构建；不改变 JavaScript 的解析、初始化和业务请求内容。静态文件仍公开，推荐等业务接口继续受密码门禁保护。

| 公开入口 | 行为 |
| --- | --- |
| `GET /web`、`/web/`、`/web/index.html` | 同一份 `no-store` 页面，引用带内容版本的脚本包。 |
| `GET /web/assets/desktop-bundle.js?v=<version>` | 返回对应保留版本，按 gzip/identity 协商编码，支持 ETag/304；未知或已淘汰版本返回 404，不替换成新内容。 |
| 原 `/web/assets/js/*`、`/shared/*` | 继续提供独立源文件，兼容移动端、插件共享代码与开发工具。 |

完整 HTTP 缓存与编码约定见 [API 模块](api.md#桌面首屏静态脚本2026-10-23)。`tests/test_desktop_asset_bundle.py` 覆盖依赖顺序、拼接语法、版本失效与淘汰、编码协商、缓存复用及鉴权边界。

## 性能采样后的启动优化（2026-10-23）

| 已实现能力 | 行为 |
| --- | --- |
| 默认简体快速启动 | 共用 i18n 运行时不下载恒等词库、不扫描原始中文 DOM；语言切换仍恢复源文本。 |
| 滚动控件合并测量 | 回到顶部控件把启动、滚动、窗口变化的布局读取合并到下一帧，避免在首屏构建中强制布局。 |

公开 API 与配置不变。真实页面、冷热缓存、语言切换及网络安全回归见[性能采样验收](../verification/2026-10-23-profiled-startup.md)。

## 启动关键路径（2026-10-23）

已实现：defer 脚本保持依赖顺序并移到 head，提前发现资源；恢复连接地址和前端偏好后立即开始鉴权，与首屏渲染重叠。隐藏的局域网鉴权／开机自启动设置不再启动即查询，首次禁用控件，打开设置后读取服务器状态。登录门禁、公开 API、配置与依赖不变。真实交替测试仅证实小幅热启动收益，冷启动未改善，详见[启动验收](../verification/2026-10-23-startup-latency.md)。

## 首屏请求调度（2026-10-23）

推荐卡片的收藏／稍后再看状态查询不再在建卡过程中同步扇出：统一在后续任务中发送，最多两个在途请求，失败也会释放名额。实际保存／取消操作保持直接调用，沿用 mutation registry 的版本保护。辅助首屏水合任务以两个 worker 独立处理，推荐、运行状态和待确认提示仍立即启动，单项失败不终止其余任务；运行状态首读结束即建立实时连接，不等待辅助面板完成。公开 API、配置、依赖和安装流程不变。性能数据与回归范围见 [整页排队优化记录](../verification/2026-10-23-web-cover-queue.md)，追加八轮真实请求对照见 [A/B 验收](../verification/2026-10-23-cover-live-ab.md)。

## 完整候选评估器选项

高级设置新增 `eval_scorer=jev`（纯文本 / 受控分类实验模式），显示独立 provider readiness
及与封面评估不兼容的说明；保存由后端统一校验。
未知主题 / IP 不由 LLM 补全。插件高级设置和移动设置提供相同模式入口，CLI 展示同一配置。
Jev 连接区可配置服务开关、完整 API URL、Token、固定模型、双上下文上限、每批候选数、
超时及传输重试。与评估模式可一次保存；Token 密码输入、留空保留、明确清除，保存后清空，
只在后端本机 TOML 保存，不进入浏览器存储/读取响应。共享
`web/shared/jev-settings.js` 的 `mount(root, evaluator).load(provider, evaluation)` / `collect()`
也供插件与移动 Web 使用。更换 URL 必须重填 Token，换模型不继承旧容量；同样支持原有 env。
readiness 与配置后台应用状态均不等于官方联网/质量验收；当前不提供付费探测按钮。

## 概述

聊天布局修复（2026-10-23）仅调整桌面 Web `/web` 的 `.chat-page`，包括安装版内打开的网页与其窄屏模式；移动 `/m`、插件 popup 和 CLI 没有该容器，不改布局。无新接口、配置、依赖或安装流程。

`src/openbiliclaw/web/desktop/` 是桌面 Web 单页应用（`/web`，入口
`index.html`，主逻辑 `assets/js/app.js` 单文件 IIFE，样式
`assets/css/app.css`）。本文档当前聚焦「聊聊口味」tab 的 agent loop
前端（M8，设计共识见
[聊一聊 Agent Loop 设计](../plans/2026-09-23-chat-agent-loop-design.md)，
后端协议见 [agent 模块](agent.md) 与 [api 模块](api.md)）。

## 推荐导演批次协议（桌面 / 移动 Web）

桌面 `/web` 与移动 `/m/` 推荐页复用
`web/shared/director-client.js`，与插件 side panel 使用同一份生成幂等、
批次归因和可见曝光策略。现有布局不变；编排策略由后端配置决定，前端不
把 `jev_recurrent`、`agent_jev`、`agent_only` 实现成三套请求逻辑。

- **读取与生成分开**：`GET /api/recommendations` 在导演启用时只恢复已提交
  批次。响应 `director.requires_generation=true` 且页面可见、没有现有卡片时，
  前端显式 `POST /api/recommendations/director/generate`，意图为 `NEXT`。
  加载更多仍调用 append（`NEXT`）；换一批仍调用 reshuffle（`STAY`）。
  只有后端明确返回 `409 stay_without_batch`，首次换一批才转成新的 `NEXT`
  请求；其余 STAY 不会被静默改成推进。
- **失败不重复消费**：生成请求 ID 按动作和来源保存在 localStorage，网络失败、
  页面重载后重试沿用同一 ID；请求在途禁用相应按钮。换批失败保留旧卡片，
  不因空响应或异常提前清空。平台 scope 由后端追加到基础 `stream_ref`，
  客户端不会把已经带平台的返回 stream 再追加一次。
- **真实可见才曝光**：卡片保留 `batch_ref`、`stream_ref`、`item_key` 与原始
  recommendation `id`，并绑定相同含义的 DOM data 属性。共享模块使用
  `IntersectionObserver`，只在卡片已挂载、与视口相交且 `document.hidden=false`
  时发送 `/api/recommendations/director/present`。获取数据、预加载、后台标签页
  和未滚到的卡片不算曝光；缺少观察器时也不伪造曝光。
- **归因和有限重试**：可见 ACK 使用稳定的 batch/推荐行 ID，失败最多重试
  两次；点赞、点踩、评论和推荐点击都携带原始推荐行 ID 与 `batch_ref`。
  恢复历史批次不重新生成推荐行。旧后端、缺少 director 元信息或 mode=off
  时保留原 API body 与推荐行为。

协议单测为 `tests/js/director-client.test.mjs`，Python 包装入口为
`tests/test_director_frontend_protocol.py`；
`tests/test_director_frontend_e2e.py` 使用真实浏览器和本地合成接口覆盖桌面、
移动首屏生成、屏外/后台不曝光、恢复不生成及反馈批次归因。插件打包通过既有
shared 资源复制流程分发同一文件。

## 已实现功能

LLM 实例设置页可选择 `api_route`（API Route），新实例预填 `gpt-5.5` 与 `https://global.api-route.com/v1`；模型发现、探测及保存沿用既有 OpenAI 兼容实例流程。首次运行 `/setup/` 向导也提供该选项与 API Key 入口。

LLM 实例设置页可选择 `cheaperinference`（Cheaper Inference），新实例预填 `gpt-5.4-mini` 与 `https://api.cheaperinference.com/v1`；模型发现、探测及保存沿用既有 OpenAI 兼容实例流程。首次运行 `/setup/` 向导也提供该选项与 API Key 入口。

| 任务 | 状态 | 说明 |
|------|------|------|
| 首屏静态脚本合并 | ✅ | 固定顺序的包内脚本经 `DesktopAssets` 变为一次 defer 请求；最多保留两个内容版本和预压缩 gzip，独立资源入口继续可用 |
| 配置应用状态恢复 | ✅ | 保存进入队列后每 2 秒查询应用状态，实时通知漏收或单次查询失败后继续恢复；终态停止查询，配置状态显示后台等待步骤/错误，保留新草稿 |
| 聊天工作区自适应 | ✅ | legacy 与带会话侧栏模式均占满主区域可用宽度，不继承内容页 1120px 上限；消息气泡仍限宽，窄屏沿用侧栏浮层和输入框定位 |
| M8 流式过程展示 | ✅ | `POST /api/chat/agent/stream` 真流式：thinking 过程文本 + 每步一行折叠工具摘要（可展开参数/结果）+ 审批卡内嵌；完成后整体折叠为「过程（N 步）」；`final` 落成答复气泡；历史回放从 `payload.agent_events` 重建同一视图 |
| M8 会话列表 | ✅ | 侧栏（新建/切换/内联改名/归档/显示已归档），默认会话徽标，`active_turns>0` 活跃圆点，标题与预览轮询刷新（复用 2.5s 共享聊天轮询），当前会话持久化到 localStorage |
| M8 skill 切换 | ✅ | 会话条 skill chip（图标+名称）弹出角色选择浮层（`GET /api/chat/skills`），按会话记忆选择、下一回合生效；`suggest_skill` 工具调用渲染为切换卡（一键切换/忽略） |
| M8 审批卡 | ✅ | `approval_request` 渲染审批卡（summary+参数+impact，批准并执行/拒绝可填理由）；侧栏「待审批」入口带未读 badge（轮询 `?status=pending`，抽屉并列展示 executing 记录）；approve 端点异步执行：批准只入队，卡片就地转「执行中…」（按钮移除防重复点击），终态由 2.5s 轮询 `GET /api/chat/approvals`（executing 列表 + 全量快照）落到「已批准并执行」（含 result 摘要）或「已批准，但执行失败」（含 error 详情）；刷新/回放时 executing 记录覆盖归约出的 pending 卡恢复中间态；旧协议（响应无 `queued` 字段、同步返回 ok/result）按 `normalizeApproveResponse` 兜底直接显示结果；回放里 `approval_result` 显示审批结局与执行结果 |
| M8 任务中心 | ✅ | 侧栏入口 + 右侧抽屉：任务列表（状态/进度/取消）、详情复用过程流组件渲染 `steps`、完成后 report + 建议清单（逐项确认：soft_write「确认执行」/ hard_write「去对话确认」，v1 统一落成来源会话里的结构化指令消息）；`start_background_task` 确认卡；`agent_task_summary` turn 渲染系统汇总卡 |
| M8 回退与兼容 | ✅ | 探测 `GET /api/chat/skills` 失败 → legacy 模式（布局与行为与 M8 前完全一致）；agent 流 503（`loop_enabled=false`）时当轮回退旧 `/api/chat/stream` 单跳流式；delight/探针内嵌聊天、假设卡片、待聊确认、对话上下文引用等旧功能不动 |
| token 级流式渲染（issue #83） | ✅ | agent 流的 `delta` 事件逐 token 追加进实时回复气泡（`handleAgentStreamEvent` 直接累加 `live.replyText`；中间跳的 `thinking` 事件清空它、文本移入过程流，`final` / `done` 全文接管）；legacy 单跳流式沿用 `content` 增量渲染，后端换成真 delta 后自动受益 |
| 会话与流结束隔离 | ✅ | SSE 必须收到 `done` 才确认完成，提前 EOF 走历史恢复；历史快照按来源会话与请求代次校验；live 回复只在来源会话展示，旧回合 `done.skill` 不覆盖用户中途切换的角色 |
| 未发送草稿按会话隔离 | ✅ | 切换前保存当前输入，切回恢复对应草稿；新会话为空，发送或清空后不复活旧文字。草稿仅保存在本页面内存，不跨刷新或设备同步 |
| 聊天风格选择 | ✅ | 顶部独立风格入口，六种单选模板、说明和同题预览；保存到当前会话，刷新/跨端同步，从新消息生效。保存期间阻止本会话抢先发送；晚到请求不覆盖新会话或已保存选择 |
| 蓝鱼实验室设置 | 🧪 | 设置页新增「养蓝色大肥鱼」Tab（`assets/js/fish-settings.js`，数据按 Tab 懒加载）：总开关 / 只预览 / 独立小号 Cookie 保存与校验 / 可编辑人设、说话规矩与可选受众（只回哪类人） / 配额 / 预览一轮 / 发一轮 / 最近账本。全部走 `/api/fish/*`，不进全局 `PUT /api/config` 保存流程，鱼的 Cookie 与实验室字段也不混进主配置表单 |
| 审批拒绝草稿 | ✅ | 与移动 Web / popup 共用 `agent-chat.js` 的拒绝编辑保留助手，轮询和过程重绘保留原因输入与焦点；终态更新不会复活旧操作按钮 |
| 聊天提示条避让 | ✅ | 聊天页提示条固定在输入区上方且不接收指针事件，避免遮挡卡片或输入区、悬停导致提示长期不消失；其他页面仍可点击关闭与悬停暂停。`scripts/browser/chat-toast-hit-test.js` 验证真实 CSS 下的点击穿透 |

配置状态接口：`GET /api/config/apply-status` 的 `state/requested_revision/applied_revision/message/error` 为权威快照。仅当前保存待应用时轮询，同一时间最多一个请求；终态和旧修订过滤沿用现有规则。此修复只涉及桌面 Web（含安装版打开的网页），移动 Web、插件 popup 与 CLI 不使用这个保存栏；不改后端应用流程、配置或安装器。

接口：`GET /api/chat/personas` 获取目录，`PATCH /api/chat/sessions/{id}` 保存 persona，
会话列表/详情中的 `metadata.persona` 是唯一持久来源。目录不可用时仅风格入口降级，
不影响已有角色和聊天；目录独立加载，不阻塞会话历史。切入 agent 模式即清除 legacy
共享历史，迟到的 bootstrap/legacy 快照不再覆盖当前会话。保存回执不确定时提示刷新或重试，
不宣称服务器未写入。

草稿隔离不新增 HTTP 接口：`selectChatSession()` 在更换会话 ID 前保存输入框的实时值，
随后同步恢复目标会话草稿，再请求历史。历史恢复和旧会话流完成不会把草稿带到其他会话。

提示条不新增接口；`body.chat-page-open` 与设置页共用输入区上方的避让位置。
三端补充实测、失败记录与修复见[三端逐项复验](../testing/2026-09-30-chat-three-surfaces-e2e.md)。

## 模块结构

`api/desktop_assets.py` 负责上述静态资源交付；前端业务源码仍按下列结构维护：

```
web/desktop/
├── index.html                       # SPA 骨架；chatPage = 会话侧栏 + 对话区 + 三个浮层
└── assets/
    ├── css/app.css                  # 末尾「聊一聊 Agent Loop（M8）」段：侧栏/过程流/卡片/抽屉样式
    └── js/
        ├── app.js                   # 主逻辑；M8 集中在「agent loop（M8）」注释段
        └── chat-agent-core.js       # M8 纯逻辑层（无 DOM）：SSE 增量解析、
                                     # agent 事件 → 过程视图模型、全部卡片/列表 markup
```

`chat-agent-core.js` 是 classic script，暴露 `globalThis.OpenBiliClawChatAgentCore`
（同时 `module.exports`，供 `tests/js/` 的 node:test 直接引用）。有意不碰 DOM、
不发请求：SSE 解析与过程模型归约可单测，DOM 胶水全部留在 `app.js`。

> 注：移动 Web / 插件 popup（M9）另有共享模块 `web/shared/agent-chat.js`
> 承载同类能力；桌面端的 markup 与桌面布局/样式深度耦合（宽屏侧栏 +
> 抽屉形态），v1 保持独立实现。后续可考虑把 SSE 解析与事件归约两层收敛到
> 共享模块，桌面只保留 markup。

桌面也加载 `/shared/agent-chat.js` 的 `captureApprovalDrafts` /
`restoreApprovalDrafts`，用于在 DOM 重绘前后保留用户正在编辑的拒绝原因。

## 关键交互接线（app.js）

- **模式探测**：首次进入聊天 tab 时 `initDesktopAgentChat()` 拉
  `GET /api/chat/skills`；成功进入 agent 模式（`chatPage.has-agent-side`
  两栏布局），失败保持 legacy 单栏布局。
- **发送**：`sendChat()` 在 agent 模式且 `scope=chat` 时转
  `sendAgentChat()`：先 `POST /api/chat/turns`（`streaming=true`，带
  `session_id` 与 `skill`）创建 pending turn，再消费
  `POST /api/chat/agent/stream` 的 SSE；每个事件经
  `OpenBiliClawChatAgentCore.createSseParser` 解析后 apply 进 live 过程模型并
  重渲染。503 时 `legacyStreamForTurn()` 复用旧单跳流式端点完成同一 turn。
  `streamAgentChatTurn()` 在缺少 `done`/明确 `error` 的 EOF 上抛出断连错误，
  不把仅有 `final` 的答复当作持久完成确认；live 对象保存发送时的会话和角色。
- **历史**：agent 模式下 `refreshDialogueTurns()` 改拉
  `GET /api/chat/sessions/{id}?limit=100`（默认会话收编 legacy turn），
  `selectDialogueTurns` 过滤口径不变；带 `agent_events` 的 turn 由
  `desktopAgentTurnMarkup()` 渲染为「用户气泡 + 折叠过程 + 答复气泡」。
  轮询重渲染时保留过程折叠组件与证据的展开状态（`agentDetailKey`）。
  异步返回必须匹配当前会话和最新请求代次，避免切换会话后的旧请求覆盖新历史。
- **事件委托**：`#chatLog` / `#chatApprovalsBody` / `#chatTaskCenterBody`
  统一走 `handleAgentSurfaceClick()`（审批 approve/reject/confirm-reject、
  skill 建议 accept/dismiss、后台任务确认卡、建议清单确认、任务详情打开）；
  拒绝就地更新卡片；批准按 approve 响应归类（`normalizeApproveResponse`）：
  新协议 `queued` → 卡片转「执行中…」并登记 `executingApprovalIds`，终态由
  `refreshChatApprovals()` 的 2.5s 轮询（pending + executing + 跟踪全量快照）
  落到卡片与 toast；旧协议同步 ok/result → 直接显示结果。服务端随后把
  `approval_result` 追加进 turn 回放数据，下一次轮询自动对齐。
- **建议清单执行（v1）**：确认一条建议 = 切到来源会话并发送一条结构化指令
  消息（动作 + 参数 JSON），soft_write 由 agent 当回合直接执行，hard_write
  自然触发审批卡；不在前端直接调写接口。

## 测试

`tests/js/desktop-chat-agent-core.test.mjs`（node:test，29 条）：SSE 分片/
CRLF/多行 data/坏帧容错、过程模型归约（thinking/tool_call/tool_result 配对、
approval_request → approval_result 结局、step_limit、error）、折叠组件 markup
（完成后默认折叠、live 展开、HTML 转义）、特殊工具卡（suggest_skill /
start_background_task）、会话/任务/审批/skill 列表 markup、异步审批协议
（`normalizeApproveResponse` queued/幂等终态/旧协议兜底、executing 卡无按钮、
`applyApprovalRecordToProcess` 中间态恢复与终态不降级）。

运行：`node --test tests/js/*.test.mjs`

`tests/js/chat-session-isolation.test.mjs` 同时验证桌面、移动和 popup 的真实切换函数：
新会话不继承输入、切回恢复各自草稿、清空/发送后的文字不复活。
[9 月 30 日完整复查](../testing/2026-09-30-chat-comprehensive-e2e.md)包含真实浏览器、
真实模型请求、后台任务失败与恢复，以及仍待优化的耗时问题。

### 网页工具和聊天笔记

当前 Agent 角色提供 search_web/read_webpage 时，继续用已有执行过程与 Markdown 链接
展示网页资料；共享渲染器也将正文中的裸 http(s) 来源转为可点击链接，跳过代码和已有
链接，保留 HTML 转义。口味伙伴/探寻师可定位、更正聊天笔记；delete_memory 走现有审批卡，
待批准不表示已删除，执行时原值冲突则失败并保留新笔记。本次无需额外页面入口。

### 多语言（v0.3.230+）

设置 → 通用 → **默认语言（所有端共用）** 提供 `简体中文` / `繁體中文` / `English`。切换后会立即预览并随保存写入
`[general].language`；桌面页在 `applyConfig()` 时读取 `GET /api/config` 的规范化 `language`，
再调用 `/shared/i18n.js` 的 `OpenBiliClawI18n.setLocale()`。界面文案按渲染后的 DOM 文本精确
匹配 `/i18n/ui/<locale>.json` 翻译，模型输出、内容标题与用户输入不翻译；`#language` 选择器
自身标记 `data-i18n-skip`，避免把「简体中文」翻成「簡體中文」。英文 tab 文案更宽，设置页
tab 栏改为按内容宽度排布并允许横向滚动。完整契约见 [多语言模块](i18n.md)。

首次设置 `/setup/` 的语言菜单在桌面和手机均可见，先选语言再填模型。语言写请求串行，
保存失败可见且可重试，配置预填不能覆盖用户刚选的语言。共享 i18n 运行时也监听动态提示属性，
并保证快速切换以最新选择为准；翻译目录下载失败后可重新请求。接口仍为 `PUT /api/config {language}`。
