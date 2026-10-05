# 「聊一聊」Agent Loop 改造 — 设计共识与实施拆分

- 日期：2026-09-23
- 分支：`feat/chat-agent-loop`
- 状态：**已实现**（M1–M10 全部完成于本分支，2026-09-23）。本文档是最终共识 + 实施拆分；文末「实现偏差校正」记录与最终实现的出入。

## 1. 定位与交互形态

改造对象是独立的「聊一聊」tab，不是 item 聊天；item/delight 反馈对话保持现有轻量路径（`/api/delight/respond`）不动。

**混合形态**：默认对话内 agent loop（思考 → 调工具 → 再思考，多跳）；长任务可转后台。

**过程展示（AI Coding 风格，Codex/Pi 式）**：
- thinking 与工具调用过程流式铺在对话上下文里；
- 进行中流式展开，完成后可折叠；默认每步一行折叠摘要，可展开看完整输入/输出；
- 最终结果在末尾。
- 步数刹车：默认上限 **64 跳**（可配置），超限收尾并向用户汇报现状；后台任务另有 token 预算。

## 2. Agent 运行时

- **自研 loop**：从 `SocraticDialogue._respond_with_tools`（`src/openbiliclaw/soul/dialogue.py:326`）的单跳循环扩展为多跳。参考 Pi agent 设计：极简内核（tool calling 循环 + 状态管理）、能力全外挂、过程全透明。
- **Provider 层补原生 function calling**：必须覆盖 `openai_compatible`（当前全部对话实例走它：deepseek/glm/sensenova）；Claude/Gemini 次之；其余 provider 保留现有 prompt 级 JSON 模拟兜底（`llm/service.py:791`）。
- **工具定义用 JSON Schema、向 MCP 看齐**，进程内 dispatcher；将来可开放成真实 MCP server。现有 `SOURCE_TOOLS`（`src/openbiliclaw/sources/tools.py`）的扁平 schema 需迁移。

## 3. 能力开放（tools）

v1 标准集约 10–12 个工具，从 OpenClaw 能力清单（`integrations/openclaw/skill.py:93` `build_openclaw_skills()`）内化：

- 画像/记忆读取（get_profile、记忆五层读取）
- 记忆写入（L1）
- 推荐查询 / 推荐反馈（L1：点赞、点踩、屏蔽）
- B 站数据查询（历史/收藏/动态）
- discovery 候选池查询
- 配置只读
- 订阅源管理（list 为 L0；create/toggle 为 L2，走审批）

**上下文策略：不塞数据，给入口。** system prompt / skill 里声明系统有哪些数据和对应工具，agent 按需检索。每轮只带当前会话近期窗口 + 极简核心画像。

## 4. 权限与双向影响

| 层级 | 内容 | 策略 |
| --- | --- | --- |
| L0 只读 | 画像、记忆、推荐、历史、配置读 | 默认开放 |
| L1 软写 | 记忆写入、推荐反馈 | 对话中默认开放 |
| L2 硬写 | 配置修改、订阅源开关/创建 | 逐项审批，复用假设卡片/pending-confirmation 骨架（`api/app.py:12685`、`:12729`），对话内嵌审批卡 |
| L3 对外动作 | 操作 B 站账号（评论/三连/关注） | v1 不做 |

**后台任务只读 + 交建议清单**：写动作回到对话里由用户确认后执行（任务预授权留 v2）。

## 5. Skill 体系

- skill = 人设 prompt + 工具白名单 + 可用数据声明；`*/SKILL.md` 目录约定（复用 `agent/skill.py:101` `discover_skills()` 骨架），用户把目录丢进 `data/skills/` 即生效。
- 会话 = 话题 × skill 绑定；会话中可切换 skill（含 agent 建议切换）。
- v1 内置 4 个：
  1. **口味伙伴**（默认）：全量 L0/L1 工具。
  2. **口味探寻师**：现有 SocraticDialogue 苏格拉底逻辑改造而来（假设卡片、结算队列保留），作为 skill 机制的试金石。
  3. **追番顾问**：bangumi 源 + 收藏/历史工具。
  4. **系统管家**：L2 工具为主，审批卡驱动。
- 历史 chat_turns 全部保留迁移可查。

## 6. 会话与任务

- **多会话**：自由话题列表，标题自动生成；所有会话共享持久记忆底座（画像、历史聊天、行为反馈）。chat_turns 已有 `session`/`scope` 字段，扩展为正式 session 实体。
- **任务是一等公民的持久对象**（Pi `pi-durable` 思路）：
  - tab 内有任务中心（进行中/已完成）；
  - 每个任务有独立执行记录（步骤日志），可恢复、可审计；
  - 完成后在主对话发汇总消息 + 建议清单；
  - 后台执行复用 `BackgroundTaskRegistry`（`runtime/task_registry.py:38`）做生命周期管理。

## 7. 前端范围

三端同时做：桌面 Web（`web/desktop/`）、移动 Web（`web/js/views/chat.js`）、插件 popup（`extension/popup/popup.js`）。popup 的过程流展示需适配小窗形态。

## 8. 技术缺口清单（探索结论）

1. provider 层无原生 tool calling（`LLMProvider.complete` 签名无 tools 参数，`llm/base.py:559`）——M1 核心工作。
2. 工具 schema 非 JSON Schema —— M1 迁移。
3. SSE 是假流式（`api/app.py:11144` 切片推送）—— M2 改为真实阶段事件（thinking/tool_call/tool_result/final）。
4. 无 agent→写操作审批门 —— M7 复用 durable turn + pending confirmation + `soul/ledger.py` 审计。
5. `AgentOrchestrator`（`agent/orchestrator.py`）是空壳 —— 本次正式填充或旁路新建 `AgentLoop`。

## 9. 里程碑拆分

| # | 内容 | 关键文件 |
| --- | --- | --- |
| M1 | JSON Schema 工具注册表 + dispatcher；`openai_compatible` 原生 FC；多跳 AgentLoop（64 跳上限，可配置） | `llm/base.py`、`llm/openai_provider.py`、`llm/service.py`、`agent/`（新 loop） |
| M2 | SSE 真流式事件模型（thinking/tool_call/tool_result/final） | `api/app.py` chat 端点、`soul/dialogue.py` |
| M3 | v1 工具集（10–12 个）+ 记忆检索工具 | `agent/tools/`（新）、`integrations/openclaw/skill.py` 清单内化 |
| M4 | Skill 加载（SKILL.md + data/skills/）+ 4 个内置 skill + 会话绑定/切换 | `agent/skill.py`、`skills/builtin/` |
| M5 | 多会话 API + 存储（session 实体、标题生成） | `storage/database.py`、`api/app.py` |
| M6 | 任务中心：durable 后台任务、执行记录、建议清单回报 | `runtime/task_registry.py`、`agent/tasks.py`（新） |
| M7 | L2 审批卡流（复用 pending-confirmation） | `api/app.py:12685` 一带 |
| M8 | 桌面 Web 前端（流式过程、任务中心、审批卡、会话列表、skill 切换） | `web/desktop/` |
| M9 | 移动 Web + popup | `web/js/views/chat.js`、`extension/popup/` |
| M10 | pytest + mypy + ruff + 文档同步（CLAUDE.md Documentation Requirements 清单） | 全仓 |

## 10. 验收标准

- `openai_compatible` provider 下多跳 loop 可用，64 跳上限生效；
- 对话内可见流式 thinking/tool_call 过程，完成后可折叠；
- 4 个内置 skill 可切换，`data/skills/` 自定义 skill 生效；
- L1 写（记忆/反馈）对话内生效；L2 动作弹审批卡，批准后执行并记 ledger；
- 后台任务从对话发起，任务中心可见，完成后回报建议清单；
- 三端 UI 可用；`pytest`、`mypy src/`、`ruff check src/ tests/` 通过。

## 11. 实现偏差校正（M10 收尾时核对）

- **工具数量**：§3 估「10–12 个」；实际 v1 标准集 15 个（read 8 / soft_write 3 / hard_write 3 + list_sources），另加 3 个元工具（`suggest_skill` / `start_background_task` / `propose_suggestion`）。
- **L2 审批骨架**：§4 计划「复用假设卡片/pending-confirmation 骨架」；最终实现为独立的 `ApprovalStore`（`{data_dir}/chat_approvals.json` 单文件状态机 + `profile_update_ledger` 审计），未改动 pending-confirmation 链路。
- **后台任务预算**：§1 写「后台任务另有 token 预算」；实际落地为独立跳数预算 `[agent] task_max_steps`（默认 32），未实现 token 预算。
- **skill 目录**：§9 M4 关键文件写 `skills/builtin/`；实际内置 skill 位于 `src/openbiliclaw/agent/skills_builtin/`（随 wheel / PyInstaller datas 分发），用户目录仍是 `{data_dir}/skills/`。
- **Claude/Gemini 原生 FC**：§2 列为「次之」；M1 只交付 `openai_compatible` 原生 FC，Claude/Gemini 及其余 provider 仍走 prompt 级 JSON 模拟兜底（符合「保留兜底」的底线要求）。
- **口味探寻师**：§5 计划「现有 SocraticDialogue 逻辑改造而来」；实现为 skill 人设叠加在共享 socratic system prompt 上（`_layer_skill_system_prompt()`），假设卡片与结算队列保留在既有链路，未做逻辑搬迁。
- **流量分级（M10 修补）**：后台任务 caller=`agent.task` 最初落入 maintenance 类，空库存时会被 park；M10 将 `agent.chat` / `agent.task` 归入交互流量（见 `llm/concurrency.py` `_INTERACTIVE_CALLERS`）。
