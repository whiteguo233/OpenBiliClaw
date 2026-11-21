# 官方 Ego Lite Windows Beta 试测

本次为动态网站发现能力增量，基线为 `aa4af184`。目标平台为 Windows 11 x64，后端运行在 Windows 本机。Ego Lite Windows Beta 由测试者从官方渠道安装。上游原生能力与 OpenBiliClaw 的最终验收分别记录。

## 在 Windows 上验证

1. 在测试机安装 [官方 Ego Lite Windows Beta](https://www.egolite.ai/document/en/docs/windows/quick-start)。完成首次引导，跳过浏览数据导入，然后退出浏览器。
2. 在新的 PowerShell 进入本分支工作区，运行底层探测。它使用本地测试页和临时 profile，第二阶段可能打开正常的 Ego 窗口。脚本在 `scripts/` 下生成诊断 JSON。

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-ego-windows-beta.ps1
   ```

   如果脚本找不到浏览器主程序，用 `-BrowserExe` 指定 `ego.exe` 的完整路径。请回传生成的 `ego-windows-beta-report-*.json`，并记录无窗口阶段是否看到闪窗。

3. 用分支的源码环境测试产品链路。依赖首次按项目安装文档准备。

   ```powershell
   uv run openbiliclaw sources ego-status
   uv run openbiliclaw sources prepare-ego
   uv run openbiliclaw start
   ```

4. 在设置看见「Ego Lite 已就绪」或「已准备，按需启动」后，在「聊一聊」提供一条公开、依赖 JavaScript 的栏目链接，核对试跑结果并审批启用。等下一轮定时执行，对照重复条目和候选数。再用测试账号验证人工接管、恢复试跑、空闲退出后的再次调度，以及重启前后的登录态。回传状态和脱敏运行记录。

## 验收门槛

| 范围 | 证据 |
| --- | --- |
| 签名和实例身份 | 源码测试覆盖签名拒绝、隔离启动参数和拒绝日常宿主。另以实际官方安装包的 Authenticode 证书标识核对预期发布者。 |
| 本机通信 | Python 和固定 Node 宿主通过真实回环 TCP 测试，覆盖认证成功、错误密钥、未认证命令及冒名服务。CDP 调用使用测试替身。 |
| Windows 文件租约 | 原生 `LockFileEx` 测试由 Windows CI 或 Windows 测试机执行。 |
| 官方二进制真实握手与无窗口启动 | 待 Windows 测试机验证安装布局、浏览器及 CLI 的签名、原生事件和窗口状态。 |
| 聊天试跑、登录接管及自然调度 | 待 Windows 测试机进行端到端验证。 |

分支首次交付时为 `incremental only`。当时的测试替身覆盖接线和失败分支，Windows 真机最终结果见[修复后的验收](#修复后的真机验收)。

本地 macOS 验证通过 Ego 与自定义 Feed 关联测试 334 项、补充的 Windows 安装和状态回归 28 项、共享来源设置测试 30 项，以及 MyPy、Ruff 和 PowerShell 语法检查。动态来源注册审计通过，实际宿主测试单独记录。

## 2026-11-21 Windows 真机复测

复测 `feat/windows-ego-lite-beta` 的 `25bf48ef`，使用 Windows 11 x64（构建号 26300）、Python 3.11.3、Node.js 24.13.0 和 Windows PowerShell 5.1。CLI 使用独立的 `OPENBILICLAW_PROJECT_ROOT`。

| 检查 | 结果 |
| --- | --- |
| `pytest -q tests/test_ego_windows.py tests/test_ego_setup_surfaces.py` | 24 项通过，覆盖 Windows 原生文件锁、进程身份、回环认证和安装状态接口。 |
| `pytest -q tests/test_windows_autostart_e2e.py` | 4 项通过，1 项因缺少打包产物而跳过。 |
| `pytest -q tests/test_custom_feed_workflow.py tests/test_custom_workflow_lifecycle.py tests/test_workflow_executors.py tests/test_browser_workflow.py` | 152 项通过。 |
| `pytest -q tests/test_ego_packaging.py tests/test_ego_workflow_wiring.py` | 33 项通过，覆盖打包资源和流程接线。 |
| `node --test tests/js/custom-sources.test.mjs` | 30 项通过。Ego Node 宿主与 PowerShell 探测脚本语法检查通过。 |
| `ruff check src/ tests/`、`mypy --platform linux src/` | 通过。直接在 Windows 平台运行 MyPy 时，适配分支报告 24 处错误，`main` 报告 35 处，涉及 POSIX API 和平台类型定义。 |
| 安装前 CLI 和探测脚本 | `ego-status` 显示 `manual_install`，`prepare-ego` 返回退出码 1，托管目录和 profile 尚未创建。探测脚本返回退出码 2，两阶段均报告 `ego_cli_not_found`。 |
| 官方安装器 | 从[官方下载入口](https://lite.ego.app/download)获取 x64 安装器 `0.6.3.17`。Authenticode 有效，发布者为 `CITRO LABS PTE. LIMITED`。首次运行等待 Windows 权限确认，随后完成系统级安装。 |

较宽的回归中，抽查的 8 项失败也在 `main` 的 `152a4253` 上复现。其中三项调用 Windows 缺少的 `fcntl` 或 `asyncio.open_unix_connection`，另五项涉及引导脚本的代理环境变量和 Bash 用例。全量 Windows `pytest` 已在出现平台相关失败后停止。

### 安装后首次复测与修复

| 检查 | Windows 11 x64 与 Ego Lite 0.6.3.17 实测 |
| --- | --- |
| 安装识别与签名 | `ego-status` 识别系统级安装；浏览器和 CLI 的 Authenticode 均有效，发布者为 `CITRO LABS PTE. LIMITED`。修复前 Windows PowerShell 子进程无法自动加载安全模块；修复后从 `$PSHOME` 显式导入系统模块，同一条 `prepare-ego` 命令通过签名步骤并进入握手。 |
| 普通实例原生通道 | 读取版本和 Default profile，创建临时 Task Space，创建空白标签、附加 CDP、执行 `Runtime.evaluate`、读取 PNG 截图并关闭临时 Task Space 均成功。浏览器级 `Browser.getVersion` 与用于校验归属的 `Browser.getBrowserCommandLine` 均返回 CDP `-32601`。 |
| 隔离宿主与无窗口启动 | 独立 PowerShell 探测的命名宿主在 CLI 调用期间断开，CLI 退出码 9，27 次主进程窗口采样均未发现可见窗口。独立复测 `--headless=new` 和仅 `--no-startup-window` 得到同样断开，宿主随后退出。窗口观测有结果，可用的无窗口宿主仍未验收通过。产品 `prepare-ego` 返回 `phase=handshake`、`ego_setup_command_failed`、`available=false`。 |
| 聊天试跑 | 直接调用生产聊天工具 `prepare_discovery_source` 和 `trial_discovery_source`，使用独立数据库和公开 Rust 博客。两次都在浏览器准备阶段失败并保留草稿，启用来源数为 0。未使用模型服务。 |
| 调度与登录接管 | 聊天工具、审批、调度和接管关联的 124 项自动化回归通过。真实 Windows 自然调度、登录接管和登录态恢复均被隔离宿主握手阻塞。 |

官方 CLI 会把探测事件写到 stderr。PowerShell 探测读取两个输出流，报告保留结构化步骤和错误码。对照试验发现 `--no-startup-window` 会使 Windows Beta 的命名服务断开，以 `--headless=new about:blank` 启动独立宿主可正常连接。Windows 原生 Node 进程的父进程为服务它的浏览器，因此用父进程 PID 和实际程序路径进行握手校验，后端核对该浏览器的创建时间。每个会话在创建 Task Space 前重新核验身份。

### 修复后的真机验收

| 检查 | Windows 11 x64 与 Ego Lite 0.6.3.17 实测 |
| --- | --- |
| 原生握手 | 产品 `sources prepare-ego` 返回 `setup_state=ready` 和 `available=true`。PowerShell 探测的隔离实例和普通实例均通过。实测覆盖 Task Space、标签页 CDP、请求与响应拦截、DOM 读取和 PNG 截图。允许页面命中 1 次，被阻止资源命中 0 次。 |
| 无窗口 | 隔离实例的 15 次主进程窗口采样均未发现可见窗口，原生 Node 的父进程和实际程序路径与本轮启动的签名浏览器一致。 |
| 聊天试跑与审批 | 实际聊天 SSE 接口经 AgentLoop 调用 `prepare_discovery_source`，对 [Rust 官方博客](https://blog.rust-lang.org/) 完成 Ego 观察和试跑，返回 2 条预览。启用卡片发出后来源仍为 0，提交审批后变为 `executed`，生成 1 个 5 分钟 Ego workflow。聊天决策和结构化方案使用确定性模型替身。 |
| 调度 | 在隔离 API 中启用 5 分钟周期，实际 `CustomWorkflowProducer.produce_if_due()` 首轮发现并入队 3 条，提前检查返回 `throttled`。第二轮约 304 秒后自然到期，宿主先空闲退出，再按需重启；第二轮发现 3 条，重复候选新增 0 条。两条运行记录均为 `ok`。未修改到期时间。 |
| 登录接管与恢复 | 独立本机 fixture 触发 `needs_login`。真实 Ego 通过接管 API 返回 PNG 截图，表单填写与点击提交成功；`resume` 试跑得到 2 条，启用后正式入队 2 条。约 127 秒空闲退出，新的浏览器进程保留登录态并再次采到 2 条，fixture 共只收到 1 次登录提交。测试凭据留在 fixture 会话内。 |

登录使用保留的 `.test` 域名和本机 fixture，只在测试进程为该域指定 DNS 测试答案和浏览器回环路由。公网 Rust 流程沿用生产网络策略。测试覆盖候选入队，真实模型评估、第三方账号和冻结安装包另行验收。测试来源已停用，测试 Task Space 和宿主已关闭。上述 Windows 源码验收范围通过。

修复后运行 Windows 原生与设置、接线、生命周期、运行历史、聊天审批和打包资源相关回归 157 项，全部通过。共享设置 JavaScript 30 项通过。Ruff、Linux 平台类型检查、Node 与 PowerShell 语法检查通过。

### 首次启动与静默安装复测

使用真实 `serve-api --host 127.0.0.1`、独立数据目录和本机已安装的 Ego Lite `0.6.3.17`。仅启动 API 进程，以隔离后台服务端口。配置态测试使用不联网的本机模型配置，窗口采样只观察测试专用 Ego 主进程。

| 条件 | 实测结果 |
| --- | --- |
| 新数据目录，尚无模型配置，系统已安装 Ego | `/api/ping` 可用，来源列表因模型未配置返回 503。Ego 仍从验签、原生握手进入 `ready`，创建专用 profile，105 次采样均未发现可见窗口。 |
| 新数据目录，有模型配置，模拟系统未安装 Ego | 将两项 Windows 路径覆盖指向隔离目录内不存在的文件。来源状态为 `manual_install`、`installed=false`，未启动 Ego 或创建托管 profile。 |
| 新数据目录，有模型配置，系统已安装 Ego | API 在约 4.31 秒响应，Ego 约 5.86 秒就绪。专用 profile 创建成功，63 次采样均未发现可见窗口，无需点选准备。 |
| 重启已准备的数据目录 | 状态为 `ready`、`available=false`。服务启动期间未唤醒 Ego，业务需要时再启动。 |

Windows 源码引导在官方 Ego 尚未安装时报告 `windows_ego_beta_manual_install`。官方 `0.6.3.17` 安装器的默认系统级首装此前触发了 UAC。上游讨论记录了 `--prefers-user --silent` 的用户级用法。本机已有系统级安装，缺少干净 Windows 环境，静默参数的干净首装未实测。[上游安装讨论](https://github.com/citrolabs/ego-lite/issues/524)

Windows 安装识别与部署准备相关回归 33 项通过。
