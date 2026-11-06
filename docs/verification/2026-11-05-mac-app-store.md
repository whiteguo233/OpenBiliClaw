# Mac App Store 后端候选实测（2026-11-05）

这是 11 月 5 日的 QA 快照；11 月 7 日已完成改名后的商店发行签名与安装包导出，最新状态见 [1.0.0 (2) 发行归档验证](2026-11-07-mac-app-store-distribution.md)。上传和审核状态仍分别核对。

## 环境与身份

Intel x86_64 macOS、Xcode 26.5、Python 3.12.12、原生 Ollama 0.40.14 官方运行时。独立 worktree `feat/mac-app-store`。测试使用新 bundle ID `com.openbiliclaw.backend` 的签名 App Sandbox App，不使用手机，不修改用户的登录项或系统 VPN。

本机有 Apple Development / Developer ID Application 签名身份；本轮归档用 Developer ID 验证沙盒运行。**这不是 Mac App Store 发行签名，尚未上传或提审。**

## 真实请求结果

测试驱动：`packaging/macos_store/smoke.py`。首次探针另用原生宿主 + 签名 C helper 确认容器内写入成功、容器外 `/tmp` 写入被拒绝、继承沙盒的 helper 真实 TCP HTTP 响应成功。完整包不是只跑 `create_app()` selftest。

| 项目 | 结果 |
|---|---|
| App / helper 权限 | 主 App 只有 sandbox + network client/server；可执行 helper 只有 sandbox + inherit；deep strict 验签通过 |
| 已确认依赖的许可证保留 | 归档内两项 GPL/LGPL 原文的 SHA-256 与实际安装包一致，记录实际版本和来源；不是完整许可审核或对应源码提供 |
| 冻结后端四进程 | API + full/discovery/recommendation/image workers 真实运行 |
| `/api/ping`、`/api/qr-info` | 200；监听 `0.0.0.0`，LAN 地址是实际接口 IP |
| `/web/`、`/m/` | 200 |
| `/shared/mobile-connection.js`、`/i18n/ui/en.json` | 200 |
| 浏览器二维码实图解码 | 真实 Chrome 打开包内 Web，二维码截图经 ZXing 解出 LAN `/m/` URL；对解码地址发真实请求为 200 |
| 同机通过实际 LAN IP 请求 `/m/` | 200；不是另一台物理设备的跨机验收 |
| 商店更新状态 / 手动 apply | `mac_app_store` / `app_store_managed`，auto update false；apply 409 |
| Web 自启动状态 | `managed_by_store_app`，提示在原生窗口设置 |
| 包内 Ollama | 独立随机 loopback 端口，`/api/version` 返回 0.40.14；不占用系统 11434 |
| 配置后重启 | 通过真实 `/api/config` 保存另一个 Ollama 对话端点，退出后重启同一隔离配置；私有 embedding 服务按原端口恢复，两次退出均停止该私有服务 |
| 显式 IPv6 绑定 | 第二次启动绑定 `::1`，真实 `http://[::1]:PORT/api/ping` 200，原生「打开 Web」按钮启用 |
| 包内模型 runner | 通过 HTTP 导入本机已有公开 GGUF 权重至测试容器：bge-m3 真实返回 1024 维非零向量，qwen2.5:0.5b 真实生成文本 |
| 完整后端恢复 | 经 `/api/config` 保存私有 Ollama 实例后，`/api/ping` 从未配置 degraded 恢复正常 |
| Tailnet 二进制 | 从原生宿主在继承沙盒内执行 `--self-test`，退出 0；不等于已完成真实入网/跨节点业务请求 |
| 原生窗口控件 | 最终包真实 AppKit 布局中，打开 Web / 启停服务 / 登录启动三个按钮有 action 且位于窗口内；等待实际「打开 Web」启用后才执行异常退出检查 |
| 宿主异常结束 | 强制结束宿主后，最终签名包的 7 个已记录后代进程（含快速自检）全部退出；EOF 清理通过 |
| 正常退出 | 原生 App 定时执行 Quit，已记录的 8 个后代进程全部退出；随后进程表确认无本轮 App 后台遗留 |

原始本机证据：`/tmp/obc-mas-e2e-full/report.json`、`runtime.log`、`/tmp/obc-mas-e2e-current/report.json`、`/tmp/obc-mas-native-ready-test/report.json`，以及双启动 `/tmp/obc-mas-restart-canonical/report.json`。公开报告不包含模型凭据、节点私钥或登录授权 URL。

最终 Intel QA 归档位于 Xcode Archives 的 `2026-11-05/OpenBiliClawBackend-1.0.0-1-QA.xcarchive`；复制后重新 deep strict 验签与真实 HTTP / 宿主异常退出检查通过。它使用 Developer ID 签名，保留 `QA` 标识，不能直接用于商店上传。

## 发现并修复的问题

1. 原始冻结入口在 Uvicorn 重放 SIGTERM 时，Python 来不及执行外层 `finally` 就退出，留下四个 worker。用退出后的真实进程表复现；现在 lifetime pipe 直接请求正常关闭，并安装信号处理保住清理链。新增真实 Uvicorn + worker 的 EOF 回归。
2. Python MIME 初始化会 stat 后尝试读取 `/etc/apache2/mime.types`，沙盒拒绝读取，导致 `/m/` 500。商店入口改用 Python 包内 MIME 表；重新签名后真实 `/m/`、共享 JS 与语言目录均 200。
3. 共享 Ollama supervisor 优先使用对话端点，切换对话模型后重启可能遗漏商店私有 embedding 服务。商店入口独立优先使用本地 embedding 地址，单测与真实配置后的双启动通过。
4. 原生窗口固定用 IPv4 loopback 探测，显式 IPv6 绑定时按钮一直不启用。现按运行态元数据中的绑定地址构造 URL，并为 IPv6 加方括号；真实 IPv6 请求和原生按钮状态通过。

相关回归批次 146 passed / 2 skipped；后续商店专属 9 passed；独立端点修正后 `test_mac_store_runtime`、`test_ollama_supervisor`、`test_packaging_entry` 合计 110 passed / 2 skipped。Ruff 与 MyPy（367 source files）通过，生成语言目录检查通过。首次本机完整 pytest：12,106 passed / 155 skipped / 2 failed（40m21s）；两项失败均为英文 UI 目录的排序、遗漏/残留文案，修复后 `tests/test_i18n.py` 34 passed，生成器检查通过。

首个提交 `7bccd46` 的 [完整 CI](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42027217012) 已通过，包括全量 tests、Windows HKCU、Firefox 构建与 Web guided-init；独立 [Apple Silicon 沙盒构建/真实 HTTP/退出清理](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42027217066) 通过。arm64 QA archive 单独标为 `not-for-upload`；它不能替代发行签名。后续修正的最终检查以 [PR #337](https://github.com/whiteguo233/OpenBiliClaw/pull/337) 为准。

## 尚未验收 / 提交条件

- 本机为 Intel，Apple Silicon 已由独立 CI 完成真实沙盒 HTTP 验证；这不是 Universal 二进制，也不是商店发行归档。
- 没有在真实 Tailnet 注册新节点，也没有公网域名和外部物理手机的跨机复测；本轮不得据二进制自检声称 Tailnet/公网端到端通过。
- 未实际注册/撤销用户登录项；`SMAppService` 仅在用户点击原生开关时调用。
- 仍需 Mac App Store 发行证书/私钥与 profile、独立 ASC App 记录、正式分发验证、截图与隐私/分级表单，然后才能提交审核。当前 Developer ID QA 归档不能替代这些步骤。
- 实际构建依赖包含 GPLv3 的 bilibili-api-python 与 LGPLv3 的 browser-cookie3；尚未完成商店分发许可、对应源码提供方式的核对，详见 [许可记录](../mac-app-store.md#第三方许可待核对)。不依据旧 App Store 案例直接断言当前版本一定可行或不可行。
