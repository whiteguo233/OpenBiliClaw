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

本轮交付状态为 `incremental only`。测试替身覆盖接线和失败分支。Windows Beta 的实际会话、窗口及登录恢复以真机为准。

本地 macOS 验证通过 Ego 与自定义 Feed 关联测试 334 项、补充的 Windows 安装和状态回归 28 项、共享来源设置测试 30 项，以及 MyPy、Ruff 和 PowerShell 语法检查。动态来源注册审计通过，实际宿主测试单独记录。
