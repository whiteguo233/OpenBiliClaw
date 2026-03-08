# M72 CLI Output Format Design

## Background

`OpenBiliClaw` 的核心 CLI 命令已经基本具备可运行能力，但当前输出风格不统一：有的命令是简单文本堆叠，有的命令已经有少量 Rich 标记，仍有部分命令停留在粗糙的占位输出。`7.2 输出格式` 的目标不是改业务逻辑，而是把 CLI 表达层收敛成统一、清晰、面向非技术用户的终端体验。

## Goals

- 用 Rich 统一 CLI 视觉语言
- 推荐列表改成卡片式展示
- 用户画像改成分区块展示
- 成功、警告、失败、占位态使用一致的状态反馈样式
- 已可用命令和 stub 命令都采用同一套展示系统

## Non-Goals

- 不修改命令背后的业务流程
- 不新增 `discover` / `chat` / `start` 的实际功能
- 不引入复杂 TUI 或交互式界面

## Approach

采用“公共渲染 helper + 命令级适配”的方式，仅重构 CLI 输出层。

### 1. 统一渲染原语

在 `src/openbiliclaw/cli.py` 中抽取一组轻量 helper：

- 页面标题：用于命令入口标题
- 分节标题：用于阶段进度或区块标题
- 状态面板：成功 / 警告 / 失败 / 占位
- 信息表格：用于配置、健康检查、认证状态
- 卡片面板：用于推荐内容

### 2. 已可用命令输出统一

这些命令改为结构化输出：

- `init`
- `profile`
- `recommend`
- `feedback`
- `auth status`
- `config-show`
- `health-check`
- `browser status`
- `browser open`
- `browser content`

其中：

- `init` 使用阶段进度 + 结果摘要
- `profile` 使用分区块展示人格描述、特质、价值观、阶段、需求
- `recommend` 使用卡片式推荐展示标题、UP 主、推荐理由、BV 号
- `config-show`、`health-check`、`auth status` 更适合表格或状态面板

### 3. Stub 命令统一占位态

这些命令保持原有业务空壳，但输出风格统一：

- `start`
- `discover`
- `chat`

统一显示：

- 功能名称
- 当前状态为“开发中”
- 推荐的下一步或当前可替代命令

## Visual Rules

- 绿色只表示成功
- 黄色只表示警告或部分完成
- 红色只表示阻断错误
- 蓝色或灰色只表示信息或开发中状态
- emoji 仅出现在一级标题或状态标题，避免干扰阅读
- 输出文案短句优先，避免堆叠内部实现细节

## Testing Strategy

以 CLI 输出回归测试为主，不测试颜色像素细节，只测试：

- 区块标题
- 状态标题
- 关键字段是否出现
- stub 命令是否使用统一占位语义

重点覆盖：

- `init` 成功 / 部分成功 / 失败
- `recommend` 有结果 / 无结果
- `profile` 已初始化 / 未初始化
- `feedback` 成功 / 参数错误
- `config-show` / `health-check` / `browser status`
- `discover` / `chat` / `start`

## Files

- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`
- Optional: `tests/test_cli_logging.py`
- Docs: `docs/v0.1-todolist.md`
- Docs: `docs/modules/cli.md`
- Docs: `docs/changelog.md`

## Risks

- 输出重构容易破坏现有 CLI 测试，需要先调整断言方式，避免对具体 Rich 标记过度耦合
- 如果 helper 抽得太多，会让 `cli.py` 变成小型 UI 框架，因此应控制在最少可复用粒度

## Acceptance

- 终端输出排版清晰，信息层次统一
- 推荐内容以卡片式展示
- 用户画像以区块展示
- 操作反馈有统一的颜色和状态语义
- stub 命令也有一致的占位态输出
