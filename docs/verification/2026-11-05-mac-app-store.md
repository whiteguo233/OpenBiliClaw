# Mac App Store 后端候选实测（2026-11-05）

## 环境与身份

Intel x86_64 macOS、Xcode 26.5、Python 3.12.12、原生 Ollama 0.40.14 官方运行时。独立 worktree `feat/mac-app-store`。测试使用新 bundle ID `com.openbiliclaw.backend` 的签名 App Sandbox App，不使用手机，不修改用户的登录项或系统 VPN。

本机有 Apple Development / Developer ID Application 签名身份；本轮归档用 Developer ID 验证沙盒运行。**这不是 Mac App Store 发行签名，尚未上传或提审。**

## 真实请求结果

测试驱动：`packaging/macos_store/smoke.py`。首次探针另用原生宿主 + 签名 C helper 确认容器内写入成功、容器外 `/tmp` 写入被拒绝、继承沙盒的 helper 真实 TCP HTTP 响应成功。完整包不是只跑 `create_app()` selftest。

| 项目 | 结果 |
|---|---|
| App / helper 权限 | 主 App 只有 sandbox + network client/server；可执行 helper 只有 sandbox + inherit；deep strict 验签通过 |
| 冻结后端四进程 | API + full/discovery/recommendation/image workers 真实运行 |
| `/api/ping`、`/api/qr-info` | 200；监听 `0.0.0.0`，LAN 地址是实际接口 IP |
| `/web/`、`/m/` | 200 |
| `/shared/mobile-connection.js`、`/i18n/ui/en.json` | 200 |
| 浏览器二维码实图解码 | 真实 Chrome 打开包内 Web，二维码截图经 ZXing 解出 LAN `/m/` URL；对解码地址发真实请求为 200 |
| 同机通过实际 LAN IP 请求 `/m/` | 200；不是另一台物理设备的跨机验收 |
| 商店更新状态 / 手动 apply | `mac_app_store` / `app_store_managed`，auto update false；apply 409 |
| Web 自启动状态 | `managed_by_store_app`，提示在原生窗口设置 |
| 包内 Ollama | 独立随机 loopback 端口，`/api/version` 返回 0.40.14；不占用系统 11434 |
| 包内模型 runner | 通过 HTTP 导入本机已有公开 GGUF 权重至测试容器：bge-m3 真实返回 1024 维非零向量，qwen2.5:0.5b 真实生成文本 |
| 完整后端恢复 | 经 `/api/config` 保存私有 Ollama 实例后，`/api/ping` 从未配置 degraded 恢复正常 |
| Tailnet 二进制 | 从原生宿主在继承沙盒内执行 `--self-test`，退出 0；不等于已完成真实入网/跨节点业务请求 |
| 原生窗口控件 | 最终包真实 AppKit 布局中，打开 Web / 启停服务 / 登录启动三个按钮有 action 且位于窗口内 |
| 宿主异常结束 | 强制结束宿主后，最终签名包的 7 个已记录后代进程（含快速自检）全部退出；EOF 清理通过 |
| 正常退出 | 原生 App 定时执行 Quit，已记录的 8 个后代进程全部退出；随后进程表确认无本轮 App 后台遗留 |

原始本机证据：`/tmp/obc-mas-e2e-full/report.json`、`runtime.log`，以及 `/tmp/obc-mas-e2e-current/report.json`。公开报告不包含模型凭据、节点私钥或登录授权 URL。

## 发现并修复的问题

1. 原始冻结入口在 Uvicorn 重放 SIGTERM 时，Python 来不及执行外层 `finally` 就退出，留下四个 worker。用退出后的真实进程表复现；现在 lifetime pipe 直接请求正常关闭，并安装信号处理保住清理链。新增真实 Uvicorn + worker 的 EOF 回归。
2. Python MIME 初始化会 stat 后尝试读取 `/etc/apache2/mime.types`，沙盒拒绝读取，导致 `/m/` 500。商店入口改用 Python 包内 MIME 表；重新签名后真实 `/m/`、共享 JS 与语言目录均 200。

相关回归批次 146 passed / 2 skipped；后续商店专属 9 passed。Ruff 与 MyPy（367 source files）通过，生成语言目录检查通过。完整 pytest：12,106 passed / 155 skipped / 2 failed（40m21s）；两项失败均为英文 UI 目录的排序、遗漏/残留文案，修复后 `tests/test_i18n.py` 34 passed，生成器检查通过。Apple Silicon 与新提交的完整 CI 结果以本次 PR 检查为准。

## 尚未验收 / 提交条件

- 当前本机只验证 Intel。新增只读 arm64 QA workflow 构建、签名、启动并发真实 HTTP 请求；它不会上传 App Store。
- 没有在真实 Tailnet 注册新节点，也没有公网域名和外部物理手机的跨机复测；本轮不得据二进制自检声称 Tailnet/公网端到端通过。
- 未实际注册/撤销用户登录项；`SMAppService` 仅在用户点击原生开关时调用。
- 仍需 Mac App Store 发行证书/私钥与 profile、独立 ASC App 记录、正式分发验证、截图与隐私/分级表单，然后才能提交审核。当前 Developer ID QA 归档不能替代这些步骤。
