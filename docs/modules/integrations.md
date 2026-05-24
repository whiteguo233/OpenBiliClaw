# 集成适配层

> 面向外部系统的薄适配层，负责把 OpenBiliClaw 现有学习与推荐能力整理成稳定的 integration 接口。

## 概述

`integrations/` 目录目前包含 `openclaw/` 子模块，用于把当前仓库里的运行时能力暴露给 OpenClaw 使用，而不改动核心业务主链。

这一层的职责不是承载新的推荐逻辑，而是：

- 复用现有 `memory / soul / discovery / recommendation / runtime`
- 裁剪内部模型，提供稳定 DTO
- 统一初始化依赖
- 把 adapter operation 包装成可注册的 skill descriptor

如果你需要给 OpenClaw 或新维护者一份完整的部署、初始化和日常使用说明，直接看：

- [OpenClaw 接入最短指南](../openclaw-quickstart.md)

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| OpenClaw bootstrap | ✅ | 新增 `build_openclaw_adapter_services()`，复用现有 API bootstrap 的依赖装配顺序；B 站四个 discovery strategy 共用 adapter database，保证内部 evaluator 能读取近期 negative exemplars；direct controller 会接入同一份 scheduler pause gate、独立 `PresenceTracker` 和 config-backed LLM module overrides；精简/旧配置缺少 `[llm].concurrency` 时回落到默认并发 3 |
| OpenClaw adapter operations | ✅ | 已提供 `sync_account / get_profile / recommend / submit_feedback / get_runtime_status` |
| OpenClaw skill descriptors | ✅ | 已提供协议中立的 skill descriptor 列表与 async handler |
| OpenClaw CLI bridge | ✅ | 已提供 `python -m openbiliclaw.integrations.openclaw.cli`，输出稳定 JSON |
| Workspace skill pack | ✅ | 仓库根目录新增 `skills/openbiliclaw-adapter/SKILL.md`，可被 OpenClaw 直接发现 |
| integration 异常边界 | ✅ | 新增 initialization / validation / operation 三类 adapter 异常 |
| adapter 单元测试 | ✅ | 覆盖 DTO 校验、operation 调用、bootstrap 共享依赖 |
| skill 单元测试 | ✅ | 覆盖 skill 名称、handler 映射与错误返回结构 |

## 公开 API

### 构建 adapter

```python
from openbiliclaw.integrations.openclaw import build_openclaw_adapter

adapter = build_openclaw_adapter()
profile = await adapter.get_profile()
recommendations = await adapter.recommend(limit=5, refresh_if_needed=True)
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
- `get_profile()`
- `recommend(limit=5, refresh_if_needed=True)`
- `submit_feedback(request)`
- `get_runtime_status()`

当前稳定 skill 名称包括：

- `openbiliclaw_sync_account`
- `openbiliclaw_get_profile`
- `openbiliclaw_recommend`
- `openbiliclaw_submit_feedback`
- `openbiliclaw_get_runtime_status`

### 通过 CLI bridge 调用

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli get-profile
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 3
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 3 --refresh-if-needed
uv run python -m openbiliclaw.integrations.openclaw.cli doctor
uv run python -m openbiliclaw.integrations.openclaw.cli emit-skill-descriptors
uv run python -m openbiliclaw.integrations.openclaw.cli submit-feedback \
  --recommendation-id 12 \
  --feedback-type comment \
  --note "方向对，但我想看更深一点。"
```

CLI bridge 返回稳定 JSON：

- 成功：`{"ok": true, "data": {...}}`
- 失败：`{"ok": false, "error": "...", "error_type": "validation_error|operation_error"}`

其中：

- `recommend --limit <n>` 默认走快路径，不触发 runtime refresh，更适合 OpenClaw 交互场景
- `recommend --limit <n> --refresh-if-needed` 会显式触发较重的刷新链路，再返回结果
- 如果显式 refresh 超时或上游请求异常，adapter 会自动回退到缓存推荐，避免 OpenClaw 会话长时间挂住
- `doctor` 用于确认 skill pack 路径、发现状态和 skill 名称列表
- `emit-skill-descriptors` 用于导出可序列化的 skill 定义，便于调试 OpenClaw 接线

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
    FeedbackRequest,
)

request = FeedbackRequest(
    recommendation_id=7,
    feedback_type="comment",
    note="方向对，但我想看更深一点。",
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
- `[scheduler].trending_refresh_hours`
- `[scheduler].explore_refresh_hours`
- `[scheduler].discovery_limit`
- `[scheduler].proactive_push_interval_seconds`
- `[scheduler].speculator_idle_interval_minutes`
- `[scheduler].pause_on_extension_disconnect`
- `[scheduler].extension_disconnect_grace_seconds`
- `[storage].db_path`

## 设计决策

1. **先 adapter，后 skill**
   skill 只是 OpenClaw 的接入外皮，核心集成边界应放在 adapter，而不是把业务逻辑直接写进 skill handler。
2. **复用现有 runtime 主链**
   推荐、学习、反馈回流仍由 OpenBiliClaw 内核负责，integration 层不复制业务流程。OpenClaw direct bootstrap 使用和 API runtime 相同的 scheduler 频率参数与后台 LLM gate；如果开启 `pause_on_extension_disconnect` 且没有浏览器插件 presence，daemon-owned 后台刷新会在宽限期后暂停，避免集成入口绕过省钱策略。
3. **协议中立**
   当前 `skill.py` 只返回 descriptor，不绑定未知的 OpenClaw SDK，避免过早引入外部硬依赖。
4. **真实 OpenClaw 接入走 skill pack，而不是 Python SDK**
   当前官方 skill 接入边界是 `skills/<name>/SKILL.md`。因此仓库内新增了真实 skill pack，并通过 CLI bridge 调现有 adapter。
5. **DTO 裁剪优先**
   integration 层只暴露 OpenClaw 真正需要的字段，降低内部模型变动对外部集成的影响。
6. **统一错误翻译**
   adapter 会把内部异常翻译为 integration 层错误类型，防止 OpenClaw 直接依赖内部实现细节。
