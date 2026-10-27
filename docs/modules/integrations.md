# 集成适配层 / Agent Bridge

API runtime generation 拥有且只拥有一个 `ExpressionCopyCoordinator`，它连接候选 admission、推荐分类 callback 与 daemon 生命周期。OpenClaw direct composition 不启动 daemon loop，因此不构造这个 coordinator：候选 inline admission 后会 await 一次最多 4 条的 durable expression-copy drain，且本次不做 split retry；首个 provider batch 中有效的文案立即持久化，剩余行保留 durable pending 交给下一次 OpenClaw 请求，返回前不留下 notify-only owner 或 provider 后台任务。API、CLI 与 OpenClaw 三个 production composition root 都向推荐引擎注入钳制后的 copy-ready 水位和 `pool_target_count`，因此正数水位会先领取补齐后能进入当前 topic 展示窗口的 pending，再按需维持 unrestricted copy-ready 水位；`0` 继续表示 legacy drain-all。`recommend(refresh_if_needed=True)` 还会把本次 source supply 与 inline evaluation 首批一起限制为 4 条，优先在 adapter 的交互等待窗口内产出可 serve 的有效 subset；若首 batch 全部无效，本次仍可能没有可 serve 行，诊断会如实保留该结果。这不是新的配置项，也不缩小 API daemon 的持续补货波次。

OpenClaw bootstrap 与 API/CLI production composition 使用同一组近期供给常量：`SearchStrategy` 在既有请求预算内把 1 个 query 设为 `order="pubdate"`、最多取 5 条。该 lane 只补检索覆盖并留下 `discovery_lane="recent"` provenance，不改变 evaluator relevance、admission 阈值或推荐排序。

> OpenClaw bootstrap 每个 runtime 只拥有一个 LLM gate，主服务、Soul 与 refresh 按对象身份共享；gate 在任何 provider 调用前从 canonical database available 初始化，candidate snapshot 与 controller readiness 持续同步 refill admission；发现评估并发不再硬编码。

> 面向外部 Agent 宿主的薄适配层，负责把 OpenBiliClaw 现有学习、推荐、画像和本地保存能力整理成稳定的 integration 接口。OpenClaw 是首个宿主名称；同一份协议也可被 Hermes、WorkBuddy 或其他能执行本地 JSON 命令的宿主消费。

## 概述

`integrations/` 目录包含两层：`openclaw/` 保存历史兼容路径和实际实现，`agent.py` 提供协议中立别名。它们把当前仓库里的运行时能力暴露给 Agent 宿主，而不改动核心业务主链。

这一层的职责不是承载新的推荐逻辑，而是：

- 复用现有 `memory / soul / discovery / recommendation / runtime`
- 裁剪内部模型，提供稳定 DTO
- 统一初始化依赖
- 把 adapter operation 包装成可注册的 skill descriptor
- 用 `agent-bridge/v2` 能力清单让宿主协商版本，而不是猜测可用功能

如果你需要给 OpenClaw 或新维护者一份完整的部署、初始化和日常使用说明，直接看：

- [OpenClaw 接入最短指南](../openclaw-quickstart.md)
- [Agent Bridge 能力与宿主接入说明](../agent-integration.md)

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| OpenClaw bootstrap | ✅ | `build_openclaw_adapter_services()` 初始化 database/memory 后立刻用 canonical available 配置共享 LLM gate，再构造任何可调用 provider 的 service；构造 `SoulEngine` 时与 API runtime 一样透传 canonical `database`、`[soul.preference].satisfaction_filter_enabled`，以及 task-scoped `preference_prompt_view / awareness_prompt_view / insight_prompt_view`（默认 `legacy / compact-v1 / legacy`）；其中 Awareness 值只控制 `soul.awareness_confusions`，普通 `soul.awareness` 固定 `legacy`，避免 integration 面使用分裂的持久化、满意度过滤或 cognition rollout 语义；暴露 adapter 前同步调用 controller 的幂等 `run_startup_maintenance()`。OpenClaw direct adapter 不启动 daemon `run_forever()`，因此不 attach `CandidateEvalCoordinator` 或 `ExpressionCopyCoordinator`、不把 producer 标为 coordinator-owned；`recommend(refresh_if_needed=True)` 专用 controller 把首轮 source supply 与 inline claim 固定限制为 4（fetch oversample=1、min eval batch=4、inline evaluator=1），后续调用再继续补货。每次 durable admission 会在同一请求内 await `RecommendationEngine.drain_pending_expression_copy(profile, limit=4, max_extra_requests=0)`：首 batch 的有效 subset 立刻成为 canonical available，未完成行保持 pending 由下一请求续补，避免在 45 秒交互窗口内递归拆分；若该 subset 非空且推荐历史为空，`recommend(refresh_if_needed=True)` 会直接 serve 已复制 pool。若首 batch 全部无效，本次仍可能返回空池并由后续请求重试。该首批限制是 bootstrap 内部策略，不新增 `config.toml` 字段，API daemon 的 coordinator、每轮 60 条 copy drain 与 4× supply oversample 保持不变。 |
| OpenClaw 近期供给接线 | ✅ | bootstrap 构造 `SearchStrategy` 时显式注入共享 `RECENT_SUPPLY_LANE_QUERIES=1` 与 `RECENT_SUPPLY_LANE_PAGE_SIZE=5`；与 API/CLI 使用同一有界 `pubdate` lane，避免 direct adapter 因 composition 漏接而失去近期供给。 |
| OpenClaw 视觉预热配置 | ✅ | bootstrap 构造 `RecommendationEngine` 时透传视觉画像、关键帧 / 弹幕开关、采样帧数、两个 fetch limit、摘要长度和共享 `BilibiliAPIClient`；默认关闭路径保持 no-op，配置模型切换后的 provenance 重建仍由引擎负责 |
| Agent Bridge adapter operations | ✅ | 已提供 `sync_account / get_profile / recommend / reshuffle / append_recommendations / submit_feedback / get_delight / respond_delight / get_runtime_status / get_activity_feed / get_platform_availability / chat / get_chat_history / profile edit / probe feedback / saved-list operations`；chat 仍显式固定为 `legacy_direct`，但新增 durable `turn_id`，不提交 API runtime 的 queue、也不持有 worker guard permit。失败转为携带安全 LLM 分类文案的 `AdapterOperationError`，不暴露原始上游细节。 |
| 推荐消费后的 durable inventory 同步 | ✅ | API 与 OpenClaw composition 都给真实 `RecommendationEngine` 注入 post-commit callback；独立 serve DB worker 把 recommendation + shown 在同一短事务提交成功后，callback 直接携带 `pool_counts_after` 更新共享 gate，不再同步重读共享连接。写失败不触发 callback，callback 失败也不改写 durable 提交结果；API 另在响应关键路径外读取精确 canonical snapshot 收敛广播。 |
| Agent capability manifest | ✅ | `get_capabilities` / `capabilities` 返回 `agent-bridge/v2`、宿主兼容名和完整 skill 名称；`skill.py` 是唯一注册表，防止核心功能只进 HTTP/UI 而漏进 Agent |
| Agent skill descriptors | ✅ | 已提供协议中立的 26 个 descriptor 与 async handler；OpenClaw 前缀保留为兼容命名 |
| 推荐导演与独立过滤 | ✅ | bootstrap 复用 `DirectorRuntime`：`jev_recurrent / agent_jev / agent_only` 三种策略与 API 一致；`recommend / append` 为 NEXT，`reshuffle` 为 STAY，全部原子提交完整屏幕；`restore` 不生成、不曝光，`present` 只确认宿主实际呈现的子集。filter 可在导演关闭时独立接到 discovery；启用配置不能静默回到 legacy。one-shot CLI 在同一 event loop 内关闭 runtime/provider、client 和数据库。 |
| Agent CLI bridge | ✅ | 已提供 `python -m openbiliclaw.integrations.openclaw.cli`，输出稳定 JSON；Hermes / WorkBuddy 可直接复用该命令或从 `integrations.agent` 导入别名 |
| Agent 主动探针闭环 | ✅ | 可拉取/响应 `interest.probe` 与 `avoidance.probe` 的 confirm/reject/defer/chat；`listen` 默认转发 delight、兴趣和避雷的候选及结果事件 |
| Workspace skill pack | ✅ | 仓库根目录新增 `skills/openbiliclaw-adapter/SKILL.md`，可被 OpenClaw 直接发现 |
| integration 异常边界 | ✅ | 新增 initialization / validation / operation 三类 adapter 异常 |
| adapter 单元测试 | ✅ | 覆盖 DTO 校验、operation 调用、bootstrap 共享依赖 |
| skill 单元测试 | ✅ | 覆盖 skill 名称、handler 映射与错误返回结构 |
| Native-save platform adapter contract | ✅ | `saved_sync` 已有 B 站直连实现和六个平台扩展代理；Agent 侧先写本地 membership，只有显式 `allow_state_changing=true` 才运行 native sync，目标和错误状态由后端 adapter 统一归一化。 |

## 公开 API

### Native-save 平台集成边界

`src/openbiliclaw/saved_sync/router.py` 的 `NativeSaveAdapter` 是收藏/稍后再看平台写入协议，和本页下方的 OpenClaw integration adapter 不是同一层。首个实现 `BilibiliNativeSaveAdapter` 只消费既有 `BilibiliAPIClient`：favorite 写 exact-title `OpenBiliClaw` 收藏夹，watch-later 写 B 站稍后再看；只有 resource-deal POST 产生的 dedicated favorite duplicate 异常视为重复成功，generic `11201` 保持失败，watch-later `90003` 视为视频不可用失败；登录失效与 GET/POST HTTP/application 限流分别输出稳定状态，不透传 Cookie、CSRF 或 response body。

稳定动作 token 与当前 Phase 1 映射如下：

| 用户意图 | Bilibili resolved action | 真实目标 |
|---|---|---|
| `favorite` | `favorite` | `B站 OpenBiliClaw 收藏夹`（按名称精确复用，不存在时创建） |
| `watch_later` | `watch_later` | `B站稍后再看` |

这张表描述 Agent bootstrap 当前注册的 native-save 路由：Bilibili 使用 API 直连，YouTube、
小红书、抖音、X、知乎和 Reddit 使用浏览器扩展 broker。扩展宿主没有在线或平台未登录时，
任务会保留真实的 `extension_required` / `login_required` / `unsupported` 状态，不能把本地
保存表述成平台同步成功。

平台 adapter 不拥有本地 membership、任务调度、重试或 HTTP 路由。`SavedSyncService` 保持
local-first 顺序，并用独立 task/item ledger 持久化每次请求结果；Agent bridge 的
`save_local` 只做本地写入，`sync_saved` 必须由宿主显式授权。真实账号写入 E2E 属于状态
变更，必须等待明确授权或测试账号。

### 构建 adapter

```python
from openbiliclaw.integrations.openclaw import build_openclaw_adapter

adapter = build_openclaw_adapter()
profile = await adapter.get_profile()
recommendations = await adapter.recommend(limit=5, refresh_if_needed=True)
```

已有宿主也可以使用协议中立别名：

```python
from openbiliclaw.integrations.agent import build_agent_adapter, build_agent_skills

adapter = build_agent_adapter()
capabilities = await adapter.get_capabilities()
skills = build_agent_skills(adapter)
```

### 构建 skill descriptors

```python
from openbiliclaw.integrations.openclaw import (
    build_openclaw_adapter,
    build_openclaw_skills,
)

adapter = build_openclaw_adapter()
skills = build_openclaw_skills(adapter)
```

当前稳定 operation 包括：

- `sync_account()`
- `get_capabilities()` — 返回 `agent-bridge/v2`、兼容宿主名和完整 skill manifest
- `get_profile()` — 返回的 `ProfileResponse` **有意**携带 `personality_portrait`
  全文，并把 `core_traits` / `deep_needs` / `top_interests` 各截断为前 5 条。这是画像
  边界的两个允许暴露画像的外部出口之一（另一个是聊天 core memory），不同于内容管线
  serializer 一律排除画像；详见 [画像使用登记表](../profile-usage.md)。
- `recommend(limit=5, refresh_if_needed=False, source_platform="", excluded_item_ids=[])`：
  输出同时携带 Bilibili 兼容别名和 `item_key/content_id/source_platform/content_type/body_text`
  等多源字段
- `reshuffle(...)` / `append_recommendations(...)`：对应当前 UI 的换一批和追加分页能力
- `restore_recommendations(stream_ref="openclaw", batch_ref="", source_platform="")`：导演开启时只恢复已提交整屏，不重新选择候选。
- `present_recommendations(batch_ref, ack_id, item_keys)`：宿主确认真实可见子集，稳定 `ack_id` 保证重试不重复曝光。工具返回或 Agent 读取内容不构成曝光证据。
- `submit_feedback(request)`：trim 后非空、最长 400 字符的 `request_id` 必填，并在 `openclaw` producer namespace 内唯一；adapter 不生成默认值。durable event 首写与 recommendation 投影提交后 operation 即成功，后续认知记录、内容反馈 owner drain 和摘要刷新只决定 `processing=completed/queued`，失败不会撤销或误报已经提交的反馈。相同 ID 携带不同 recommendation/type/note 会返回 validation conflict，绝不覆盖第一份 payload。
- `get_delight()` / `respond_delight(request)`：支持 `view/like/dislike/dismiss/chat`；like/dislike/dismiss 要求稳定 `request_id`
- `get_runtime_status()`
- `get_activity_feed()` / `get_platform_availability()`：复用当前 API 的活动流和平台库存快照
- `chat(request)` / `get_chat_history()`：成功返回 `ChatResponse`，并通过显式 `legacy_direct` 保持原有
  学习行为（queue submit=0，不承诺 queue 串行/receipt/guard）；失败抛出包含
  `safe_llm_failure_message()` 分类文案的 `AdapterOperationError`，同时用
  durable `turn_id` 记录 pending/completed/failed 生命周期。它仍然
  不取得 API runtime 的 queue/guard 所有权。
- `get_next_probe()` / `respond_interest_probe(request)`：兴趣假设支持
  `confirm/reject/defer/chat`
- `get_next_avoidance_probe()` / `respond_avoidance_probe(request)`：避雷假设支持
  `confirm/reject/defer/chat`
- `get_profile_edit_state()` / `edit_profile(request)`：复用确定性画像 overlay 编辑协议
- `save_local(request)` / `list_saved(...)` / `remove_saved(request)`：本地优先收藏和稍后再看
- `sync_saved(request)`：显式授权后运行 native-save task；默认拒绝外部账号写入
- 其他 operation 失败统一翻译为 `AdapterOperationError`；LLM 对话额外使用
  `safe_llm_failure_message()` 分类文案，并用 `raise ... from exc` 保留内部 cause
  供日志/调试，不序列化该 cause。API runtime 的 `202 processing` 与 30 秒卡片轮询
  不适用于该 operation

当前稳定 skill 名称包括：

- `openbiliclaw_sync_account`, `openbiliclaw_get_profile`, `openbiliclaw_get_capabilities`
- `openbiliclaw_recommend`, `openbiliclaw_reshuffle`, `openbiliclaw_append_recommendations`
- `openbiliclaw_restore_recommendations`, `openbiliclaw_present_recommendations`
- `openbiliclaw_submit_feedback`, `openbiliclaw_get_delight`, `openbiliclaw_respond_delight`
- `openbiliclaw_get_runtime_status`, `openbiliclaw_get_activity_feed`,
  `openbiliclaw_get_platform_availability`
- `openbiliclaw_chat`, `openbiliclaw_get_chat_history`
- `openbiliclaw_next_probe`, `openbiliclaw_respond_interest_probe`
- `openbiliclaw_next_avoidance_probe`, `openbiliclaw_respond_avoidance_probe`
- `openbiliclaw_get_profile_edit_state`, `openbiliclaw_edit_profile`
- `openbiliclaw_save_local`, `openbiliclaw_remove_saved`, `openbiliclaw_list_saved`,
  `openbiliclaw_sync_saved`

### 通过 CLI bridge 调用

导演默认关闭，下面已有命令保持 legacy 行为。开启 `[director].mode` 后，推荐、追加与换一批
必须携带稳定 `--request-id`（1–128 字符，每次用户动作一个 ID，重试复用），可指定
`--stream-ref` 和 `--explicit-intent`；每屏允许 1–20 张卡。响应及每张卡携带
`batch_ref / stream_ref`，反馈可用 `--batch-ref` 验证精确归属。NEXT 推进体验步骤；STAY
保留根方向且排除整个换屏链。相同配置的短命令重建不会清掉未来计划，配置变化会拒绝旧进程
继续生成。已有提交屏幕始终不可重写。
如果新增避雷偏好使旧屏不再可展示，restore 或同 ID 重试返回空项与
`requires_generation=true`，不修改原屏、不偷偷分配下一屏；下一次生成须由宿主显式发起。

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 4 --request-id screen-1
uv run python -m openbiliclaw.integrations.openclaw.cli restore
uv run python -m openbiliclaw.integrations.openclaw.cli append --limit 4 --request-id screen-2
uv run python -m openbiliclaw.integrations.openclaw.cli reshuffle --limit 4 --request-id replacement-2
# 仅在人确实看到卡片后，用返回的真实标识确认；不是工具返回后自动确认。
uv run python -m openbiliclaw.integrations.openclaw.cli present \
  --batch-ref RETURNED_BATCH_REF --ack-id visible-1 --item-key RETURNED_ITEM_KEY
```

没有可见性确认能力的宿主应保持未曝光；用户真实 like/dislike/comment 等动作通过原有
canonical event ingress 入库，再投影至该屏会话，既不等待 Soul 异步分析，也不重复写学习事件。
Python 长生命周期宿主应在退出时 `await adapter.aclose()`；CLI 自动完成清理。独立过滤的
`off / shadow / enforce` 配置与校准约束详见 [discovery 模块](discovery.md)，不依赖导演开关。

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli get-profile
uv run python -m openbiliclaw.integrations.openclaw.cli capabilities
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 3
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 3 --source-platform zhihu
uv run python -m openbiliclaw.integrations.openclaw.cli reshuffle --limit 3
uv run python -m openbiliclaw.integrations.openclaw.cli append --limit 3
uv run python -m openbiliclaw.integrations.openclaw.cli activity-feed
uv run python -m openbiliclaw.integrations.openclaw.cli platform-availability
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 3 --refresh-if-needed
uv run python -m openbiliclaw.integrations.openclaw.cli doctor
uv run python -m openbiliclaw.integrations.openclaw.cli emit-skill-descriptors
uv run python -m openbiliclaw.integrations.openclaw.cli next-probe
uv run python -m openbiliclaw.integrations.openclaw.cli next-avoidance-probe
uv run python -m openbiliclaw.integrations.openclaw.cli respond-avoidance-probe \
  --domain "浅层热点复读" \
  --response confirm
uv run python -m openbiliclaw.integrations.openclaw.cli respond-interest-probe \
  --domain "建筑美学" \
  --response defer
uv run python -m openbiliclaw.integrations.openclaw.cli respond-delight \
  --bvid BV1xxx \
  --response like \
  --request-id delight-BV1xxx-like-1
uv run python -m openbiliclaw.integrations.openclaw.cli chat-history --session openclaw
uv run python -m openbiliclaw.integrations.openclaw.cli submit-feedback \
  --recommendation-id 12 \
  --feedback-type comment \
  --request-id openclaw-feedback-12-comment-1 \
  --note "方向对，但我想看更深一点。"
```

CLI bridge 返回稳定 JSON：

- 成功：`{"ok": true, "data": {...}}`
- 失败：`{"ok": false, "error": "...", "error_type": "validation_error|operation_error"}`

其中：

- `recommend --limit <n>` 默认走快路径，不触发 runtime refresh，更适合 OpenClaw 交互场景
- `recommend --limit <n> --refresh-if-needed` 会显式触发较重的刷新链路，再返回结果
- 如果显式 refresh 超时或上游请求异常，adapter 会自动回退到已有历史；历史为空时继续尝试 serve 已完成文案的 canonical pool。若没有任何已复制的 canonical 行（例如首 batch 全部无效），会如实返回空列表而不伪造推荐
- `doctor` 用于确认 skill pack 路径、发现状态和 skill 名称列表
- `capabilities` 用于宿主启动时协商 `agent-bridge/v2` 和完整能力清单；后续新增核心功能必须同时加入 descriptor、CLI（若适合）和该清单
- `emit-skill-descriptors` 用于导出可序列化的 skill 定义，便于调试 OpenClaw 接线
- `submit-feedback` 的 `--request-id` 是 required；同一个动作的 CLI/agent 重试必须复用，skill descriptor 同样把 `request_id` 列入 required，并声明 `minLength=1/maxLength=400`
- `next-probe` / `next-avoidance-probe` 会返回下一条待确认的正向兴趣 / 避雷假设，并记录本次 domain / axis，避免连续拉取重复候选
- `respond-interest-probe` 和 `respond-avoidance-probe` 都支持 `confirm|reject|defer|chat`；`defer` 会返回 snooze 时间和次数，`chat` 会进入带主题上下文的对话
- `respond-avoidance-probe --response confirm|reject|defer|chat` 中，`confirm` 写入不喜欢方向并触发候选池清理，`reject` 只进入冷却
- `save-local` / `list-saved` / `remove-saved` 只操作本地 membership；`sync-saved` 必须显式传 `--allow-state-changing`，避免 Agent 误写外部账号

### Workspace Skill Pack

当前仓库根目录新增：

- `skills/openbiliclaw-adapter/SKILL.md`

这是按 OpenClaw 官方 skill 目录约定提供的真实 skill pack。它不会直接实现业务逻辑，而是指导 OpenClaw 通过上面的 CLI bridge 调用 adapter。

`SKILL.md` 现在同时包含：

- Docker 优先 / 本地兜底的部署决策
- 项目安装前置步骤
- 首次 `openbiliclaw init` 初始化要求
- `doctor` 自检命令
- 常规推荐应优先走快路径的调用规则

### DTO 与错误类型

```python
from openbiliclaw.integrations.openclaw import (
    AdapterOperationError,
    AdapterValidationError,
    AvoidanceProbeFeedbackRequest,
    FeedbackRequest,
)

request = FeedbackRequest(
    recommendation_id=7,
    feedback_type="comment",
    note="方向对，但我想看更深一点。",
    request_id="openclaw-feedback-7-comment-1",
)

avoidance = AvoidanceProbeFeedbackRequest(
    domain="浅层热点复读",
    response="confirm",
)
```

输入输出不会直接暴露 `SoulProfile`、数据库 row 或 `MemoryManager` 原始状态文件结构。

## 配置项

OpenClaw integration 本身没有新增独立 `config.toml` 段落，直接复用现有运行时配置：

- `[general]`
- `[llm]`
- `[bilibili]`
- `[scheduler]`
- `[storage]`

其中最直接影响 adapter 行为的项包括：

- `[general].data_dir`
- `[bilibili].cookie`
- `[scheduler].pool_target_count`
- `[scheduler].account_sync_interval_hours`
- `[scheduler].refresh_check_interval_seconds`
- `[scheduler].signal_event_threshold`
- `[scheduler].trending_refresh_minutes`
- `[scheduler].explore_refresh_minutes`
- `[scheduler].discovery_limit`
- `[scheduler].proactive_push_interval_seconds`
- `[scheduler].speculator_idle_interval_minutes`
- `[scheduler].avoidance_speculation_interval_minutes`
- `[scheduler].avoidance_speculation_ttl_days`
- `[scheduler].avoidance_speculation_cooldown_days`
- `[scheduler].avoidance_speculation_confirmation_threshold`
- `[scheduler].avoidance_speculation_max_active`
- `[scheduler].pause_on_extension_disconnect`
- `[scheduler].extension_disconnect_grace_seconds`
- `[storage].db_path`

## 设计决策

1. **先 adapter，后 skill**
   skill 只是 OpenClaw 的接入外皮，核心集成边界应放在 adapter，而不是把业务逻辑直接写进 skill handler。
2. **复用现有 runtime 主链**
   推荐、学习、反馈回流仍由 OpenBiliClaw 内核负责，integration 层不复制业务流程。OpenClaw direct bootstrap 不运行 daemon 的 `run_forever()`，但会在返回可调用 adapter 前执行同一个 controller startup-maintenance hook；若宿主随后也启动该 controller，幂等标记避免重复维护。它仍使用和 API runtime 相同的 scheduler 参数、后台 LLM gate，以及预算内 `1×5` 的 B 站近期供给 lane；但因没有启动 candidate / expression coordinator task，`recommend(refresh_if_needed=True)` 的 controller refresh 使用固定 4 条首批 source/evaluation wave，并在 admission 后同步 drain 至多 4 条 durable copy、禁用本请求内的 split retry：有效 subset 立即可服务，未完成行留作下一请求补齐，不会把候选交给未运行的 owner。API daemon 的 30 条 coordinator worker wave 与 60 条 copy drain 不受影响。
3. **协议中立**
   当前 `skill.py` 只返回 descriptor，不绑定未知的 OpenClaw SDK，避免过早引入外部硬依赖。
4. **真实 OpenClaw 接入走 skill pack，而不是 Python SDK**
   当前官方 skill 接入边界是 `skills/<name>/SKILL.md`。因此仓库内新增了真实 skill pack，并通过 CLI bridge 调现有 adapter。
5. **DTO 裁剪优先**
   integration 层只暴露 OpenClaw 真正需要的字段，降低内部模型变动对外部集成的影响。
6. **统一错误翻译**
   adapter 会把内部异常翻译为 integration 层错误类型，防止 OpenClaw 直接依赖内部实现细节。
