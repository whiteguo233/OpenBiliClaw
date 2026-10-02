# 桌面 Web（Desktop Web UI）

## 概述

`src/openbiliclaw/web/desktop/` 是桌面 Web 单页应用（`/web`，入口
`index.html`，主逻辑 `assets/js/app.js` 单文件 IIFE，样式
`assets/css/app.css`）。本文档当前聚焦「聊聊口味」tab 的 agent loop
前端（M8，设计共识见
[聊一聊 Agent Loop 设计](../plans/2026-09-23-chat-agent-loop-design.md)，
后端协议见 [agent 模块](agent.md) 与 [api 模块](api.md)）。

## 已实现功能

LLM 实例设置页可选择 `api_route`（API Route），新实例预填 `gpt-5.5` 与 `https://global.api-route.com/v1`；模型发现、探测及保存沿用既有 OpenAI 兼容实例流程。首次运行 `/setup/` 向导也提供该选项与 API Key 入口。

LLM 实例设置页可选择 `cheaperinference`（Cheaper Inference），新实例预填 `gpt-5.4-mini` 与 `https://api.cheaperinference.com/v1`；模型发现、探测及保存沿用既有 OpenAI 兼容实例流程。首次运行 `/setup/` 向导也提供该选项与 API Key 入口。

| 任务 | 状态 | 说明 |
|------|------|------|
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
| 审批拒绝草稿 | ✅ | 与移动 Web / popup 共用 `agent-chat.js` 的拒绝编辑保留助手，轮询和过程重绘保留原因输入与焦点；终态更新不会复活旧操作按钮 |
| 聊天提示条避让 | ✅ | 聊天页提示条固定在输入区上方且不接收指针事件，避免遮挡卡片或输入区、悬停导致提示长期不消失；其他页面仍可点击关闭与悬停暂停。`scripts/browser/chat-toast-hit-test.js` 验证真实 CSS 下的点击穿透 |

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
