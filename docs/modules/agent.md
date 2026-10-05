# 聊天 Agent Loop（聊一聊）

## 概述

`src/openbiliclaw/agent/` 承载「聊一聊」对话的多跳 agent 运行时（设计共识：
`docs/plans/2026-09-23-chat-agent-loop-design.md`）。M1 交付后端核心三件：
JSON Schema 工具注册表、provider 原生 function calling、多跳 `AgentLoop`；
M2 把 loop 接上了聊天 SSE 端点（真流式）；M3 交付 14 个 v1 标准工具；
M4 交付 skill 体系（SKILL.md 加载、4 个内置 skill、会话绑定与切换）；
M6 交付任务中心（durable 后台任务 + 建议清单回报）；
M7 交付 L2 审批门（hard_write 工具逐项审批 + 审计台账）；
M8 交付桌面 Web 前端（`web/desktop/`）：agent loop 真流式过程展示
（`assets/js/chat-agent-core.js` 桌面侧 SSE 解析与过程流渲染，503 回退旧
`/api/chat/stream` 单跳流式）、会话侧栏、skill 切换、审批卡与任务中心，详见
[desktop-web 模块](desktop-web.md)；
M9 交付移动 Web（`web/js/views/chat.js`）与插件 popup（`extension/popup/`）
两端前端：agent loop 真流式过程展示（`web/shared/agent-chat.js` 共享 SSE 解析
与过程流渲染，503 回退旧单跳流式）、会话列表、skill 切换、审批卡与任务中心，
详见 [extension 模块](extension.md)（桌面 Web 为 M8）。

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| M1 JSON Schema 工具注册表 | ✅ | `agent/tools/registry.py`：`Tool`（name / description / JSON Schema parameters / permission_level / handler）+ `ToolRegistry`（注册、按 skill 白名单 `subset()`、按权限 `filter_by_permission()`、`llm_schemas()` 渲染 OpenAI 格式、`legacy_schemas()` 渲染旧扁平格式、参数校验 + 同步/异步 dispatch） |
| M1 SOURCE_TOOLS 迁移 | ✅ | `agent/tools/source_tools.py` 是 create_source / list_sources / toggle_source 的唯一事实来源（JSON Schema + 权限级：list=read，create/toggle=hard_write）；`sources/tools.py` 保留 `SOURCE_TOOLS` 旧扁平结构与 `SourceToolDispatcher` 同步接口，委托同一组 handler |
| M1 原生 function calling | ✅ | 见 [llm 模块](llm.md)：OpenAI 系 chat-completions flavor 原生 FC，其余 provider 走 prompt 模拟兜底 |
| M1 多跳 AgentLoop | ✅ | `agent/loop.py`：`AgentLoop.run()` 异步生成器逐跳产出事件，默认 64 跳上限（`[agent]` 配置），超限后无工具收尾汇报 |
| M2 SSE 流式接线 | ✅ | 新端点 `POST /api/chat/agent/stream` 真流式转发 `AgentEvent`；`SocraticDialogue.stream_agent_reply()` 复用 persona prompt / 历史 / 学习队列；除 `delta` 外的 loop 事件随 turn 落 `payload.agent_events`；旧 `/api/chat` 与 `/api/chat/stream` 保持共存 |
| M2.5 token 级流式（issue #83） | ✅ | `LLMProvider.stream_complete()` / `stream_complete_with_tools()`（基类一次性回退，OpenAI 系 chat-completions flavor 真流式）→ `LLMService.stream_complete_with_native_tools()` / `stream_socratic_dialogue()` → `AgentLoop` 每跳产出 `delta` 事件（工具调用阶段内容同样逐 token 流出，该跳若带 tool_calls 则由 `thinking` 全文接管）→ SSE `delta` 事件（不落 `agent_events`）；`SocraticDialogue.respond_stream()` 让 legacy `/api/chat/stream` 与 CLI `chat` 在无工具配置下逐 token 输出（工具回合保持一次性最终回复）；registry 流式链只在**首个 delta 之前**允许 fallback（已吐字后失败直接上抛，避免重复文本） |
| M3 扩展工具集（17 个） | ✅ | 见下文「v1 标准工具集」：`AgentToolContext` + `build_agent_tool_registry()` 总装，read / soft_write / hard_write 三级权限，handler 全部防御性降级 |
| 公开网页与聊天笔记 | ✅ | search_web/read_webpage 按需联网并保留来源；agent_notes 可检索、CAS 更正和审批删除，后续聊天按权限注入有界引用 |
| 会话聊天风格 | ✅ | 六种表达模板独立于功能角色；会话持久化、发送时冻结，三端选择与示例预览，工具权限保持 |
| M4 skill 加载与切换 | ✅ | `agent/skill.py`：`SkillDefinition` + `*/SKILL.md` 解析（手写 frontmatter 子集，无 YAML 依赖）+ `load_skill_catalog()`（内置 → `data/skills/` 覆盖，非法文件跳过记日志）；4 个内置 skill；`suggest_skill` 元工具 + 端点 skill 绑定，见下文「Skill 体系（M4）」 |
| M6 任务中心（durable 后台任务） | ✅ | `agent/tasks.py`：`AgentTaskRunner` 在 `BackgroundTaskRegistry` 登记的 asyncio task 里跑**只读** AgentLoop（`filter_by_permission("read")` ∩ skill 白名单），事件逐步落 `agent_tasks.steps`；写动作只经 `propose_suggestion` 元工具产出结构化建议清单，完成后往来源会话写汇总消息；交互侧另有 `start_background_task` 元工具（同 suggest_skill 确认卡模式）。见下文「任务中心（M6）」 |
| M7 L2 审批门 | ✅ | `agent/approvals.py`：loop 拦截 hard_write 调用 → `approval_request` SSE 事件 + durable 审批记录（JSON 文件存储，免迁移）；`/api/chat/approvals` 端点批准（立即返回 + 后台任务二次 dispatch 真执行，幂等）/拒绝；审计落 `profile_update_ledger`。见下文「L2 审批门（M7）」 |

## 聊天风格：与功能角色独立

`agent/persona.py` 定义六个表达模板：`natural` 自然朋友（默认）、`concise` 简洁直接、
`warm` 温柔倾听、`playful` 轻松幽默、`analytical` 理性分析、`socratic` 循循善诱。
`GET /api/chat/personas` 返回标题、说明、同题回复示例与 `example_prompt`；模板不增加
工具，所有权限仍由所选 skill 决定。用户可以组合任意功能角色和聊天风格。

会话选择通过 `PATCH /api/chat/sessions/{id}` 的 `persona` 写入 `metadata.persona`；
创建 agent chat turn 时冻结到服务端保留字段 `payload.agent_persona`，SSE、重试和
后台恢复均读取冻结值。缺失或旧存量风格按 natural 处理，非法新选择返回 422。
切换只影响新消息，不触发配置重载或额外 LLM 调用。

公开 helpers：`resolve_chat_persona()` 读取兼容默认，`validate_chat_persona()` 校验
新输入，`chat_persona_instruction()` 返回可选表达层。natural 维持原 prompt；显式
其他风格优先于旧全局语气中冲突的表达规则，用户当前篇幅要求与事实、工具、审批规则
始终优先。所有模板都保持简单问题简答，不强制追问或展开分析。

## 模块结构

```
agent/
├── loop.py              # AgentLoop + AgentEvent（多跳循环与事件模型，含 M7 审批拦截）
├── approvals.py         # ApprovalStore + ApprovalRecord（M7 审批台账，JSON 文件持久化）
├── orchestrator.py      # 既有空壳编排器（未接 loop）
├── persona.py           # 独立表达模板目录（不授予工具权限）
├── skill.py             # SkillDefinition / SkillCatalog / SKILL.md 加载（M4）
│                        # + 既有 Skill ABC / SkillRegistry 代码技能骨架（未使用）
├── tasks.py             # AgentTaskRunner + propose_suggestion / start_background_task 元工具（M6）
├── skills_builtin/      # 4 个内置 skill 的 SKILL.md（随包分发）
│   ├── taste-companion/   # 口味伙伴（默认）
│   ├── taste-explorer/    # 口味探寻师
│   ├── bangumi-advisor/   # 追番顾问
│   └── system-steward/    # 系统管家
└── tools/
    ├── registry.py      # Tool / ToolResult / ToolRegistry / validate_tool_arguments
    ├── source_tools.py  # 订阅源管理三工具的 JSON Schema 定义与 handler
    ├── common.py        # 共享错误类型（组件缺失）与输出辅助
    ├── context.py       # AgentToolContext + build_agent_tool_registry（v1 总装）
    ├── skill_tools.py   # suggest_skill 元工具（agent 建议切换 skill）
    ├── web_search_tools.py  # search_web：Exa 公开 MCP
    ├── web_reading.py       # read_webpage：公开 URL 正文读取
    ├── profile_tools.py     # get_profile
    ├── memory_tools.py      # read_memory / write_memory / delete_memory / search_history
    ├── recommendation_tools.py  # get_recommendations / query_discovery_pool
    ├── bilibili_tools.py    # get_watch_history（本地数据层）
    ├── feedback_tools.py    # submit_feedback / save_item（soft_write）
    └── config_tools.py      # get_config（脱敏只读）/ update_config（白名单真写入，M7）
```

## 公开 API

### AgentLoop

```python
from openbiliclaw.agent.loop import AgentLoop, AgentEvent
from openbiliclaw.agent.tools import ToolRegistry

loop = AgentLoop.from_config(llm_service, tool_registry, config)
# 等价于 AgentLoop(llm_service, tool_registry,
#                  max_steps=config.agent.loop_max_steps,
#                  tool_result_max_chars=config.agent.tool_result_max_chars)

async for event in loop.run(
    system_instruction="你是口味伙伴…",
    user_message="帮我看看我订阅了什么",
    history=[{"role": "user", "content": "…"}, ...],
    tools=tool_registry.subset(["list_sources"]),  # 可选：本次运行的白名单子集
):
    print(event.type, event.to_dict())
```

事件类型（`AgentEvent.type`）：

- `thinking`：带工具调用的中间跳里模型输出的文本（`text`）。最终答复**不重复**
  发 thinking——没有工具调用的跳只发 `final`。
- `tool_call`：一次工具调用（`tool_name` / `arguments` / `summary` 一行摘要 / `step`）。
- `tool_result`：工具执行结果（`text` 已按 `tool_result_max_chars` 截断，
  `ok` 区分成功/失败，`truncated` 标记截断）。未知工具名与参数校验失败都以
  `ok=false` 的结果回填给模型，让模型自我纠正。
- `approval_request`（M7）：hard_write 调用被审批门拦截、**未执行**时发出
  （`approval_id` / `tool_name` / `arguments` / `summary` / `impact`），
  随后紧跟一条 `tool_result`（回填给模型的「等待审批」说明）。仅在 loop
  接线了 approval gate 时出现；未接线时保持 M1 直执行为。
- `step_limit_reached`：达到 `max_steps` 时发出一次，随后 loop 追加一条收尾指令
  并做一次**无工具**调用，让模型汇报进展。
- `final`：最终回复文本，每个 run 恰好一个（正常收尾或步数上限收尾）。

`step` 从 1 开始编号；LLM 调用失败直接抛给调用方（M2 映射为 SSE error 事件），
工具失败不抛出。`run()` 内部维护 canonical OpenAI 消息列表（assistant 消息带
`tool_calls`，结果用 `role="tool"` + `tool_call_id` 回填），对原生 FC 与 prompt
模拟两条 service 路径透明。

### ToolRegistry

```python
from openbiliclaw.agent.tools import Tool, ToolRegistry, build_source_tool_registry

registry = build_source_tool_registry(database)
registry.register(Tool(
    name="save_note",
    description="把一条观察写入记忆",
    permission_level="soft_write",   # read / soft_write / hard_write
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    handler=lambda args: "已记下",    # 同步或 async 均可
))

registry.llm_schemas()      # OpenAI tools 格式，给原生 FC
registry.legacy_schemas()   # 旧扁平格式（SOURCE_TOOLS 兼容）
registry.subset([...])               # skill 白名单子集
registry.filter_by_permission("soft_write")  # 权限上限过滤

result = await registry.dispatch("save_note", {"text": "..."})
# ToolResult(ok, content, error) — error ∈ "" / unknown_tool /
# invalid_arguments / handler_error / async_handler
result = registry.dispatch_sync("save_note", {...})  # 旧同步调用方
```

参数校验是 JSON Schema 子集（`type` 含联合类型、`properties`、`required`、
`enum`、`items`、`additionalProperties: false`），刻意不做全量实现；handler 内
部仍保留各自的防御性检查。`permission_level` 是过滤能力 + M7 审批门依据：
接线 approval gate 的 loop 会把 hard_write 调用拦截为待审批记录。

### v1 标准工具集（M3）

`AgentToolContext`（`agent/tools/context.py`）是一个轻量 dataclass，持有
工具所需的运行时组件引用（`database` / `soul_engine` / `memory_manager` /
`recommendation_engine` / `config` / `event_ingress` / `saved_sync_service`，
字段名与 `api/runtime_context.py` 对齐，生产接线在后续里程碑完成）。
`build_agent_tool_registry(ctx)` 总装全部 14 个工具；除源管理三工具
（构造期绑定 database，无 database 时不注册）外，所有工具始终注册，
组件缺失时 handler 抛 `ToolComponentUnavailableError`，由 dispatch 映射为
机器可读的 `handler_error` 结果回填模型。

| 工具 | 权限 | 说明 |
|------|------|------|
| `get_profile` | read | 当前生效画像（洋葱模型，`SoulEngine.get_profile()` ⊕ 用户覆盖，markdown 渲染） |
| `read_memory` | read | 记忆五层/core 摘要；layer=agent_notes 按 key/keyword 检索明确保存的笔记，offset 分页；精确查询保留完整 layer/key/value，转义导致过长时改用原文输出 |
| `search_web` | read | 按公开 query 搜索网页，返回标题、完整 URL、摘要与来源，失败不伪装成无结果 |
| `read_webpage` | read | 阅读 HTTP(S) 公开 HTML/文本，逐跳校验并绑定公开 IP，返回最终 URL、标题、正文和截断状态 |
| `search_history` | read | 历史对话（`chat_turns` 新增 `Database.search_chat_turns()`）+ 行为事件（`query_events`）关键词/时间范围检索 |
| `get_recommendations` | read | 推荐池头部只读预览（`get_pool_candidates` / `get_pool_candidates_for_platform`），返回内容身份与完整链接供保存使用，不消耗池、不标记已展示 |
| `get_watch_history` | read | 本地内容历史（clicked/shown/removed 投影）与收藏/稍后再看清单，返回内容身份及实际推荐记录 ID（如有），不触发真实抓取 |
| `query_discovery_pool` | read | discovery 候选池库存：可服务数、待处理数、有货平台、可选抽样 |
| `get_config` | read | 配置只读，api_key/cookie/token/password 等键递归打码 |
| `list_sources` | read | 订阅源列表，含 ID、查询词/URL，支持区分同名订阅后精确开关 |
| `write_memory` | soft_write | 写记忆到各层 `agent_notes` 命名空间（event/preference/awareness/insight），更正现有项必须提供匹配的 expected_value，不覆盖引擎字段，soul 层禁写 |
| `submit_feedback` | soft_write | 推荐反馈（like/dislike/dismiss/comment），复用 `POST /api/feedback` 同款 durable 事件流入（event_ingress 幂等 + 推荐行投影 + 轻量认知钩子） |
| `save_item` | soft_write | 本地收藏/稍后再看（`SavedSyncService.save_local(auto_sync=False)`，不同步平台账号） |
| `delete_memory` | hard_write | 按 layer/key/expected_value 删除一条聊天笔记，批准时再次核对原值；不删除聊天历史或系统画像 |
| `create_source` | hard_write | 创建订阅源（M1 已有） |
| `toggle_source` | hard_write | 订阅源开关（M1 已有） |
| `update_config` | hard_write | 配置修改（M7 起真写入）：白名单内已存在标量键（文本/数字/布尔），密钥类与路径/存储类一律拒绝；批准后经 API `config_update_hook` 提交单字段补丁，复用设置页保存锁、应用队列与 last-good 回滚；实际热重载失败会返回失败并结束审批为 failed |

大体量画像/历史由工具按需查询，结果有长度上限。少量显式保存的聊天笔记
通过 `render_agent_notes_prompt()` 作为有界 JSON 引用进入当前用户上下文；只有
chat scope 且当前角色含 read_memory 才注入，不进入 system 指令或学习原始消息。

内容读取工具在每条摘要后提供 JSON「定位信息」：`content_id` /
`source_platform` / `content_url` / `content_type` / `title` / `author_name`
保留原始身份和完整链接，供模型继续调用 `save_item`。历史投影中存在真实
`recommendation_id` 时一并返回，供 `submit_feedback` 使用；未展示的推荐池
候选不伪造推荐记录 ID，也不能把列表序号或缓存键用作反馈 ID。

### Skill 体系（M4）

chat skill = 人设 prompt + 工具白名单 + 可用数据声明，载体是
`*/SKILL.md` 目录约定。加载入口 `load_skill_catalog()`（`agent/skill.py`）：
先读内置目录 `agent/skills_builtin/`（随 wheel / PyInstaller 分发），再叠加
用户目录 `{data_dir}/skills/`——同名 skill 用户版覆盖内置版并记 info 日志；
非法文件跳过并记 warning，不影响启动。frontmatter 是手写的 YAML 子集解析器
（项目无 PyYAML 依赖）：仅支持 `key: value` 标量、`- item` 块列表与
`[a, b]` 行内列表。

```markdown
---
name: taste-companion        # 必填，slug [a-z0-9][a-z0-9-]*
title: 口味伙伴               # 可选显示名
description: 一句话说明        # 必填
tools:                        # 可选白名单；缺省 = 无工具
  - get_profile
  - write_memory
---
正文即 system prompt：人设 + 可用数据与工具入口声明（必填，非空）。
```

`SkillDefinition`（name / description / system_prompt / tools / title /
source=builtin|custom）与 `SkillCatalog`（`get` / `default` /
`to_public_list` / `render_switch_guide`）是纯数据层，不持有运行时组件。

内置 4 个 skill 及工具白名单：

| name | 显示名 | 工具白名单 |
|------|--------|-----------|
| `taste-companion` | 口味伙伴（默认） | 原有查询/反馈/收藏 + search_web/read_webpage + 可审批的 delete_memory |
| `taste-explorer` | 口味探寻师 | get_profile / read_memory / write_memory / delete_memory / search_history / submit_feedback / search_web / read_webpage（访谈角色） |
| `bangumi-advisor` | 追番顾问 | get_profile / read_memory / get_recommendations / get_watch_history / save_item / submit_feedback / search_web / read_webpage |
| `system-steward` | 系统管家 | list_sources / get_config / search_web / read_webpage + hard_write 三件套（create_source / toggle_source / update_config；人设强调改动需用户批准） |

**会话绑定与切换**：`POST /api/chat/agent/stream` 请求体新增可选 `skill`
字段（空 = 默认口味伙伴，未知名返回 422）。选中 skill 后：loop 的工具集 =
`agent_tool_registry.subset(skill.tools)` + `suggest_skill` 元工具；system
prompt = 按当前请求调整深度的共享聊天人设 ⊕ skill 人设 ⊕ 其他 skill 清单
（`_layer_skill_system_prompt()`，dialogue.py）⊕ **Agent 工作纪律**
（`_AGENT_LOOP_GROUND_RULES`，所有 skill 共享的三条硬约束：① 工具纪律——
已有对话足以回答时无需为补充背景查询画像或记忆，仅查必要数据并复用本轮结果；
需要查询或修改数据必须实际发起 tool_call，严禁在正文描述/编造工具调用与结果，没调
工具就不得声称查过/改过；② 记忆归属——记忆/画像/历史来自跨会话共享底座，
无明确依据不得断言行事发生在本对话；③ 会话边界——「本对话/第一回合」指
当前会话，上下文窗口只是当前会话近期）。会话中切换就是下一回合带
新的 `skill` 值；**agent 建议切换**走 `suggest_skill` 元工具
（`agent/tools/skill_tools.py`）：模型输出工具调用（`skill` + `reason`），
前端把该 `tool_call` 事件渲染成切换卡片，用户确认后以下一回合的 `skill`
字段生效——agent 自身不能切换。skill 列表见 `GET /api/chat/skills`。

共享聊天人设由 `build_socratic_dialogue_prompt(..., socratic=False)` 提供：简单问题
直接答清楚，寒暄/致谢一句结束，简单事实给结果；尊重用户指定长度，每句只保留必要要点，
不把长串逗号/括号当作简答。摘要默认不输出内部置信度/权重，但保留影响结论的不确定性；
复杂任务充分分析并执行必要步骤。澄清只针对影响
正确性/执行的缺口或当前访谈角色的探索需求，不给每个角色叠加无条件的苏格拉底追问。
`taste-explorer` 自身的访谈职责不变。此调整在原有模型调用中生效，没有关键词路由
或额外分类调用，不降低工具、步数、输出 token 或实例 reasoning 预算。

### 服务层入口

`AgentLoop` 只依赖 `LLMService.complete_with_native_tools()`（协议见
`SupportsNativeToolCompletion`）：路由到支持原生 FC 的 provider 时走 native
链，否则展平消息走 prompt 模拟。详见 [llm 模块](llm.md)。

### SSE 事件协议（M2，`POST /api/chat/agent/stream`）

请求体复用 durable turn 结构（与 `POST /api/chat/turns` 相同的字段：
`message` 必填，`turn_id` / `session` / `scope` 可选），M4 起另有可选
`skill` 字段（默认口味伙伴，未知名 422）。带 `turn_id` 时完成
该 pending durable turn（`streaming=True` 创建）并把整段事件流落库；不带
`turn_id` 则为临时运行（不落库）。

响应是 `text/event-stream`，每个 `AgentEvent.to_dict()` 一条 SSE event，
**event 名 = `type`**：

| event | data 字段 | 语义 |
|-------|-----------|------|
| `thinking` | `type` / `step` / `text` | 带工具调用的中间跳里模型输出的文本；无工具调用的跳只发 `final`，不重复发 thinking |
| `tool_call` | `type` / `step` / `tool_name` / `arguments` / `summary` | 一次工具调用；`summary` 是一行折叠摘要（如 `list_sources()`） |
| `tool_result` | `type` / `step` / `tool_name` / `text` / `ok` / `truncated` | 工具执行结果（已按 `tool_result_max_chars` 截断）；`ok=false` 表示未知工具 / 参数校验失败 / handler 异常。hard_write 被拦截时该事件携带的是「已提交审批、等待批准」说明而非真实执行结果 |
| `approval_request` | `type` / `step` / `approval_id` / `tool_name` / `arguments` / `summary` / `impact` | M7：hard_write 调用已登记为待批准动作（**未执行**），前端据此渲染审批卡（做什么 = `summary`+`arguments`，影响 = `impact`），用户批准后调 `POST /api/chat/approvals/{approval_id}/approve` |
| `delta` | `type` / `step` / `text` | token 级增量片段（token streaming）：当前跳 assistant 文本的一个增量，供前端实时渲染；可能出现在任何跳（含最终跳）。中间跳的 delta 文本稍后由该跳的 `thinking` 全文事件接管，最终跳的 delta 由 `final` 全文接管——权威文本始终以 `thinking` / `final` 为准，旧客户端可安全忽略本事件。**不落库**（见下） |
| `step_limit_reached` | `type` / `step` / `text` | 达到步数上限时发一次，**随后必跟一个 `final`**（无工具收尾汇报） |
| `final` | `type` / `step` / `text` | 最终答复，每个 run 恰好一个；发完后流进入收尾 |
| `done` | `reply` / `turn_id` / `skill` | 终端事件（端点级，非 loop 事件）；`reply` 即 `final.text`，`skill` 是本回合实际生效的 skill 名 |
| `error` | `error` | 失败的唯一事件（安全文案），发出后流结束；LLM 异常时带 `turn_id` 的 turn 置为 `failed`。租约准入超时（30 秒预算）按 paused/active 区分「正在重载配置」与「对话通道正忙」，此时 loop 未开始、turn **保持 pending** 并由兜底 worker 在 lane 恢复后重跑 agent loop 完成 |

`step` 从 1 开始编号。turn 落库时关键步骤（含 thinking / tool_call /
tool_result / approval_request / step_limit_reached / final）以相同 dict 结构写入
`chat_turns.payload.agent_events`（JSON 数组，免迁移），历史回放直接读
`GET /api/chat/turns/{turn_id}` 的 `payload.agent_events`；`delta` 事件只服务
实时渲染，**不写入** `agent_events`（避免每轮数百条片段撑大 turn 行），回放文本
由 `thinking` / `final` 全文重建。
streaming turn 创建时服务端在 payload 写入 `agent_stream`（+ 可选 `agent_skill`）
标记（属客户端不可伪造的保留键）：同进程交互流断连后，API 持有的执行任务
继续运行原 loop，逐事件落 `agent_events` 并完成 turn。重复请求在对话租约内
重新读取终态，直接重放已存事件和回复。尚未开始执行或进程重启留下的 pending
turn 由 durable worker 使用原 skill 补答；进程崩溃仍遵循至少一次恢复语义，
不能把网络断连的去重保证理解为跨崩溃的工具 exactly-once 保证。
未知 turn 返回 404，消息、会话或 skill 冲突返回 409。

### L2 审批门（M7）

系统管家在用户改动目标和参数明确后直接调用写工具生成审批卡；该调用只提交
提案。用户在卡片上批准才触发执行，无须先在聊天中重复口头确认。回复中仅在
取得真实审批 ID 后声称卡片已生成。

hard_write 工具（create_source / toggle_source / update_config）在 agent loop
里**绝不直接执行**：接线了 approval gate 的 `AgentLoop` 拦截这类调用，把
「待批准动作」登记进 `ApprovalStore`（`agent/approvals.py`），流出
`approval_request` 事件，并把「此操作需用户批准，已提交审批 #id」作为
`tool_result` 回填给模型——**当前回合正常结束**，不在 SSE 流中间挂起等待。
用户批准后，approve 端点把记录迁移到 `executing` 并**立即返回**，
真实执行由 `BackgroundTaskRegistry` 登记的后台任务（`chat_approval_execute`，
热重载 `cancel_all` 豁免）完成：用登记时的原 arguments 二次 `registry.dispatch`
执行真写入，完成后迁移终态、写审计台账，并把一条 `approval_result` 事件追加进
来源 turn 的 `payload.agent_events`，使历史回放能看到审批结局。执行解耦是
刻意的：update_config 会触发热重载的 lane 排空，可能等待数分钟，绝不能在
HTTP 请求内同步执行；前端轮询 `GET /api/chat/approvals` 观察
`executing → executed / failed` 的进展。

审批登记前先校验工具 JSON Schema；参数错误作为 `ok=false` 的工具结果
回填模型修正，不生成注定执行失败的审批卡。

**存储**：`ApprovalStore` 是单 JSON 文件存储（`{data_dir}/chat_approvals.json`，
tmp + os.replace 原子写，进程内 threading.Lock 串行化），刻意不动
`storage/database.py`（免迁移）；`path=None` 时为纯内存（测试）。生产接线
（`api/runtime_context.py`）在热重载间**复用同一 store 实例**：文件从
`Config.data_path` 定位，按规范路径比较，项目相对路径和符号链接别名（例如
macOS 的 `/tmp` 与 `/private/tmp`）指向同文件时仍保留实例，避免配置批准
触发重建时误走崩溃恢复、把 `executing` 回退为 `approved`，保证状态机只有
一份内存权威；读路径（get/list）只在惰性
过期真的改变了记录时才落盘，避免热重载窗口内并存的旧实例把文件写回旧态。
记录字段：approval_id（`ap_*`）/ tool_name / arguments / summary（做什么）/
reason（为什么，取参数的 `reason`）/ impact（影响说明，来自 `Tool.impact_hint`）/
session / session_id / turn_id / status / 时间戳 / result / error。

**状态机**：`pending → approved → executing → executed`（执行成功）/
`failed`（执行失败，终态，`error` 携带原因）、`pending → rejected`、
`pending → expired`（默认 24h TTL，读取/写入时惰性过期）。幂等：重复 approve
已 approved/executing/executed/failed 的记录直接返回现状（**不重执行、不重复
入队**）；重复 reject 同理且不再写审计。`mark_executing` 只接受 approved 态、
`mark_executed` 只接受 executing 态，从机制上禁止二次执行；端点侧另有一把
asyncio 锁把 approve→mark_executing→入队串成原子段（锁内不含 dispatch）。
崩溃恢复：进程在 `executing` 期间退出时，加载会把记录降回 `approved`
（副作用是否发生不确定，由用户重新 approve 重试，绝不自动重跑）。

**审计**：每次真实决策（批准执行成功/失败、拒绝）经 `ProfileLedger`
（`soul/ledger.py`）写 `profile_update_ledger` 一行：`write_point` =
`agent.approval.<tool>`，`source` = `chat_agent_loop`，`before` 携带
approval_id + arguments + summary，`after` 携带 status + result，
`gate_verdict` = approved/rejected，`held_id` = approval_id，`turn_id` 溯源。
best-effort：台账写入失败不阻塞审批动作。

**端点**（详见 [api 模块](api.md)）：`GET /api/chat/approvals`
（`?status=&limit=`）、`POST /api/chat/approvals/{id}/approve`、
`POST /api/chat/approvals/{id}/reject`（body 可带 `reason`）。

**update_config 白名单**：批准后的真写入只允许「已存在的普通标量键」——
目标字段当前值必须是 str/int/float/bool（新值按当前类型 coercion，布尔接受
true/false/1/0/yes/no/on/off）；键任一段命中密钥类标记（api_key / cookie /
token / secret / password / credential / sessdata / access_key）或路径/存储类
标记（dir / path / file / database 分词匹配，外加显式 `data_dir`）一律拒绝。
写入顺序：内存副本校验 → API `config_update_hook(key, value)` → 锁内加载最新磁盘配置并应用单字段补丁 → 快照/保存 → 同一 config apply queue → 等待实际生效或回滚。成功才更新 last-good；等待期间不改 live Config，失败/被较新修订替代均返回失败。脱离 API 的兼容调用仍使用 persist/reload hooks；reload 失败时补偿保存旧配置并尝试恢复旧运行时，保留原始错误。任一补偿失败会分别报告文件或运行时恢复失败，避免把部分写入当作已完整回滚。

落盘前必须通过 `validate_runtime_config`，无效 provider 等配置不能破坏现有
可用配置；校验失败与持久化失败均回滚原值。非有限数字（NaN/Infinity）和
私有属性路径一律拒绝。白名单、字段与类型校验失败均返回 `ok=false`，使
审批准确落到 `failed`，不会显示为已经执行成功。

### 任务中心（M6，durable 后台任务）

任务是一等公民持久对象（`agent_tasks` 表，见
[storage 模块](storage.md)）：独立于来源会话存在，带完整执行记录，可审计；
终态包括 `completed` / `failed` / `cancelled` / `interrupted`。

**只读 + 建议清单**：`AgentTaskRunner`（`agent/tasks.py`）为每个任务构造一个
独立 `AgentLoop`（共享当前 `llm_service`，caller=`agent.task`，**不**绕过全局
并发闸；M10 起 `agent.chat` / `agent.task` 归入交互流量类，用户显式发起的
任务不会被空库存的 refill 保留位 park，见
[llm 模块](llm.md#runtime-全局补货优先-admission)），工具集 = `agent_tool_registry.filter_by_permission("read")`
∩ skill 白名单（任务带 `skill` 时），另加 `propose_suggestion` 元工具。后台
loop 物理上没有写工具；所有写意图只能通过 `propose_suggestion(action, summary,
payload)` 落成结构化建议（action ∈ write_memory / submit_feedback / save_item /
create_source / toggle_source / update_config，上限 20 条，summary ≤500 字符、
payload ≤4000 字符）。

**生命周期**：`POST /api/chat/tasks` 落 `pending` 行并立即在
`BackgroundTaskRegistry.track("agent_task.<task_id>")` 登记的后台 asyncio task
里开跑；每个 loop 事件实时 `append_agent_task_step` 落库（上限 200 条 /
200_000 字符，单条 text ≤2000 字符，超限以 `steps_truncated` 标记收尾）；
完成时 `set_agent_task_report` CAS 落 `completed` + report + suggestions，
并往来源会话写一条 `payload.type="agent_task_summary"` 的 durable chat turn
（`message` 为 `[后台任务完成] <标题>`，`reply` 为报告 + 建议清单导读），
前端据此渲染汇总卡。汇总卡 payload 是 server-owned：`ChatTurnIn` 保留键拒绝
客户端提交 `agent_task_summary` / `task_id` / `task_status` 或
`payload.type="agent_task_summary"`（M10），防止伪造任务汇总卡。LLM 异常落 `failed`（同样写回失败说明）；用户取消落
`cancelled`（不写回消息）；**服务重启/热重载**把仍在 pending/running 的行标为
`interrupted`（终态，重启恢复在 `create_app` 启动时执行一次；热重载经 registry
cancel_all 取消在途任务，与显式取消区分靠 runner 的 `_cancel_requested` 集合）——
任务**不自动恢复**，由用户从任务中心重新发起。

**交互侧发起**：对话内 loop 可调用 `start_background_task(prompt, title?,
skill?)` 元工具（read 级、无副作用，注册进每个 skill 子集，同 `suggest_skill`
模式）：模型产出 `tool_call` 事件 → 前端渲染确认卡 → 用户确认后前端调
`POST /api/chat/tasks` 真正发起。v1 不做 agent 自动派发。

**预算**：后台任务用独立的更小跳数预算 `[agent] task_max_steps`（默认 32，
交互对话是 `loop_max_steps` 64），超限同样走 step_limit_reached → 收尾汇报。

### 接线与并发

- `RuntimeContext._rebuild_components()` 在构造 `SocraticDialogue` 后同步构造
  `ctx.agent_loop = AgentLoop.from_config(llm_service,
  build_agent_tool_registry(agent_tool_context), config, caller="agent.chat",
  bypass_semaphore=True, approval_gate=chat_approval_store)`（M4 起从 M1 的源管理
  三工具升级为全量 v1 工具集；M7 起挂审批门），同时暴露
  `ctx.agent_tool_registry`（端点按 skill 做 `subset()`）、`ctx.agent_tool_context`
  （M7：update_config 的统一 apply hook 与兼容 persist/reload hook 载体）、`ctx.chat_approval_store`
  （M7：`{data_dir}/chat_approvals.json`）与 `ctx.skill_catalog`
  （内置 + `data/skills/`），随热重载原子 swap；`ctx.config_reload_delegate`
  由 `create_app` 启动时一次性指向 `_rebuild_runtime_with_lane_handoff`，
  不随 rebuild 覆盖。
- API 的 `_bind_runtime_lane_dependencies()` 在启动和热重载后把 app-owned
  `EventIngressService` 接到当前 `agent_tool_context.event_ingress`，确保反馈工具
  使用与 HTTP 反馈端点相同的持久化、幂等和认知唤醒入口；同时接入统一
  `config_update_hook`，批准后的配置修改与设置页使用同一应用和回滚事务。
- 端点在 `DialogueExecutionCoordinator` 租约内运行整个 loop（与旧单跳路径
  串行），历史与学习由 `SocraticDialogue.stream_agent_reply()` 在
  `_respond_lock` 下完成：user turn 先 append（失败回滚）、socratic system
  prompt 作为 loop 的 system instruction、完成后 append agent 答复并按
  learning mode 提交学习任务。
- Agent 短期历史按 `session_id` 从 durable turns 恢复，长期画像和记忆共享；
  canonical binding 同时进入模型 prompt、学习任务和回复后的对象结算。
- `[agent] loop_enabled = false` 时端点返回 503；旧端点不受影响。

## 配置

见 [配置参考](config.md) 的 `[agent]` 段：`loop_enabled`（默认 true）、
`loop_max_steps`（默认 64）、`tool_result_max_chars`（默认 4000）、
`session_title_enabled`（默认 true）、`task_max_steps`（M6 后台任务跳数预算，
默认 32）。

### 公开网页与笔记操作边界

`search_web` 使用固定 Exa 公开 MCP 端点 `https://mcp.exa.ai/mcp`，复用现有
Exa 结果解析器与 `[network]` 出站路由，不依赖本机 mcporter 或新增密钥配置。公开端点有上游限流，依据 [Exa 官方 MCP 文档](https://exa.ai/docs/get-started/exa-mcp)。
仅发送模型为本次请求给出的 query；没有后台自动附加画像、历史或笔记的逻辑。
上游限流、协议错误、超时与真正空结果分别报告，不隐式串联多家服务拉长等待。
HTTP 客户端和流式响应共用一个异步上下文，按响应、客户端的逆序关闭，外层总时限继续覆盖完整请求。

`read_webpage` 只读取公开 HTTP(S) 的 HTML/纯文本；不会执行页面 JavaScript、
携带登录 Cookie、访问私网或读取本地文件。每次重定向重新校验地址，连接固定
到已验证的公开 IP，同时验证原域名 TLS 证书。体积、时间与跳转数均有上限。
搜索摘要和网页正文都是引用资料，不成为工具授权或修改记忆的指令。

三端继续使用现有工具过程、Markdown 来源链接及审批卡；CLI `chat` 和 OpenClaw
legacy 对话不切换到 AgentLoop，不在本次新增工具覆盖范围。后台任务保持 read-only，
可查询公开网页，但不会直接写/删记忆。
