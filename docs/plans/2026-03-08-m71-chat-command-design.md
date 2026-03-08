# M71 Chat Command Design

## Background

`SocraticDialogue` 已经具备多轮对话、历史积累和降级回复能力，但 `openbiliclaw chat` 仍然是 CLI stub。当前缺口不在对话内核，而在没有把现有对话能力接到 CLI 入口。

## Goal

把 `openbiliclaw chat` 从占位命令改成交互式 REPL，对接 `SocraticDialogue`，让用户可以在终端里进行连续的苏格拉底式对话。

## Non-Goals

- 不做会话持久化到磁盘
- 不做流式输出
- 不在这轮自动把对话结果回写偏好层或画像层
- 不新增 `chat "..."` 单轮参数模式

## Command Behavior

### Preconditions

- 运行时配置完整
- 用户画像已初始化

如果画像尚未初始化：

- 命令退出码为 `1`
- 明确提示先执行 `openbiliclaw init`

### Main Flow

1. 构建 `SoulEngine`
2. 构建 `SocraticDialogue`
3. 打印欢迎语和退出提示
4. 进入 REPL 循环
5. 每轮读取用户输入，调用 `respond()`
6. 输出 agent 回复

### Exit Conditions

- 输入 `exit`
- 输入 `quit`
- 输入空行
- `Ctrl+C`
- `EOF`

上述情况都按正常退出处理，不输出堆栈。

## CLI Output

沿用 `7.2` 的 Rich 风格，但保持对话界面简洁：

- 标题：`苏格拉底式对话`
- 启动提示：如何退出
- 用户输入提示：`你：`
- Agent 回复前缀：`阿花：`
- 退出提示：`对话结束`

## Testing Strategy

CLI 测试覆盖三条主路径：

1. 画像未初始化时提示 `init`
2. 输入一轮消息后输出 `阿花：...`
3. 输入 `exit` 时正常结束

继续使用 fake soul engine / fake dialogue，不依赖真实 LLM。

## Files

- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`
- Docs: `docs/modules/cli.md`
- Docs: `docs/v0.1-todolist.md`
- Docs: `docs/changelog.md`

## Acceptance

- `openbiliclaw chat` 不再是 stub
- 能进入多轮对话循环
- 支持 `exit` / `quit` / 空行退出
- 未初始化画像时给出清晰引导
- 文档同步更新
