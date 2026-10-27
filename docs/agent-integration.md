# Agent Bridge 接入说明

OpenBiliClaw 的 Agent 集成现在以 `agent-bridge/v2` 为稳定协议。OpenClaw 是历史宿主名，
Hermes、WorkBuddy 和其他能执行本地命令或读取 JSON skill descriptor 的宿主可以复用同一套
实现；仓库不为每个宿主复制一份业务逻辑。

## 启动协商

宿主启动时先执行：

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli capabilities
```

返回的 `skill_names` 是当前可用能力的权威清单。也可以从 Python 使用协议中立别名：

```python
from openbiliclaw.integrations.agent import build_agent_adapter, build_agent_skills

adapter = build_agent_adapter()
capabilities = await adapter.get_capabilities()
skills = build_agent_skills(adapter)
```

`openbiliclaw_` skill 前缀保留给已有 OpenClaw 配置；它不代表 payload 只能由 OpenClaw 使用。

## 当前能力面

- 多源推荐：推荐、换一批、追加、平台范围、排除项；内容使用 `item_key/content_id/source_platform`，同时保留 `bvid/up_name` 兼容字段。
- 推荐导演：可配置 Jev 逐对编排、Agent 提前编排 + Jev 修补、纯 Agent 提前编排；共用整屏提交、恢复与实际呈现确认合同，独立 Jev 过滤也接入 bootstrap。
- 完整候选评估：`discovery.eval_scorer="jev"` 复用同一纯文本 Jev evaluator，含旧推荐池补评；不要求开启导演，不隐式回退 LLM。需在宿主后端进程配置 provider 与环境变量凭据，失败保持待评估。
- 反馈与主动发现：推荐反馈、惊喜卡片的 view/like/dislike/dismiss/chat、兴趣和避雷探针的 confirm/reject/defer/chat。
- 画像与对话：画像摘要、完整 overlay 编辑状态、确定性编辑、带 durable `turn_id` 的 Socratic chat 和历史读取。
- 运行态：runtime status、activity feed、平台库存可用量、账号同步。
- 本地保存：local-first favorite/watch-later membership；native sync 是单独的、必须显式授权的动作。
- 主动推送：`listen` 默认监听 delight、兴趣和避雷候选及结果事件。

## 写操作边界

`[director].mode` 默认 `off`，旧推荐调用不变。开启后，`recommend / append / reshuffle`
必须传稳定 `request_id`（1–128 字符）：前两者推进下一屏，后者保持当前方向换整屏；同一个
动作重试复用原 ID，不能换参数。`stream_ref` 标识独立体验流，返回 `batch_ref` 冻结整屏身份。
`restore` 只读，不推进、不生成；`present` 只接受宿主确认实际可见的 `item_keys` 子集和
稳定 `ack_id`。**工具返回、Agent 读到卡片不等于用户看到，不能自动调用 present。**
宿主无法确认实际呈现时保留未曝光，真实反馈可以隐式确认对应卡片。

Python 宿主退出时调用 `await adapter.aclose()`，CLI 在同一事件循环自动关闭；相同配置的
one-shot 重建保留未执行计划，配置切换会拦截旧进程的生成请求。推荐反馈传返回卡片的
`recommendation_id` 和 `batch_ref`，只投影既有 durable event，避免重复学习。

推荐反馈和 delight 反应的 durable 写入使用稳定 `request_id` 做幂等键；同一个 ID 不得复用
到另一条内容或另一种动作。`save-local` 只写本地 SQLite；`sync-saved` 会触发 Bilibili
API 或浏览器扩展 broker 的外部账号写入，只有用户明确授权后才可以传
`allow_state_changing=true`（CLI 对应 `--allow-state-changing`）。

Agent chat 保留 CLI/OpenClaw 的 `legacy_direct` 学习所有权，以兼容旧宿主；它不会接管 API
runtime 的 settlement queue 或 worker guard。若数据库支持 `chat_turns`，adapter 会先写
pending，再完成或失败，并在重试时复用已完成的 `turn_id`。

## 新功能同步清单

以后新增一个对外核心功能时，必须在同一个变更中完成：

1. 在 `src/openbiliclaw/integrations/openclaw/operations.py` 增加稳定 operation 和 DTO 校验。
2. 在 `src/openbiliclaw/integrations/openclaw/skill.py` 注册 descriptor、输入 schema 和 handler。
3. 如果适合脚本调用，在 `cli.py` 增加命令；更新 `capabilities` 的测试，不允许只更新文档。
4. 为 durable 写操作定义幂等/授权边界，并补 adapter、skill、CLI 契约测试。
5. 更新 `docs/modules/integrations.md`、`docs/openclaw-quickstart.md`、本页和 `skills/openbiliclaw-adapter/SKILL.md`；若跨模块数据流变化，同时更新架构文档和变更日志。
6. 运行 `ruff format`、`ruff check`、`mypy src/` 和相关 `pytest`。

`openbiliclaw_get_capabilities` 是宿主发现能力的唯一入口；不要在 Hermes、WorkBuddy 或
其他宿主目录里维护第二份静态能力表。
