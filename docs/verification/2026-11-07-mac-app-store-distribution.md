# OpenBiliClaw 商店发行归档验证（2026-11-07）

代码提交：`00bb7c3cadd2833adacd3ce135a2ee10629809b5`。商店营销版本为 `1.0.0`、build `2`；共享后端仍为 `0.3.233`。本轮只修改原生产品名称与对应构建、冒烟入口，复用上一轮同源冻结后端。

## 产品与发行包

| 项目 | 实际结果 |
|---|---|
| 安装后的 App | `OpenBiliClaw.app` |
| 名称与可执行文件 | `CFBundleName`、`CFBundleDisplayName`、`CFBundleExecutable` 均为 `OpenBiliClaw` |
| 原生窗口与菜单 | 窗口标题、菜单辅助功能名称、中文/英文退出项均使用 `OpenBiliClaw` |
| 内部标识 | `com.openbiliclaw.backend`；内部 helper 名称保留技术用途 |
| Intel QA 归档 | `OpenBiliClaw-1.0.0-2-QA.xcarchive`，Developer ID 签名 |
| Intel 商店归档 | `OpenBiliClaw-1.0.0-2-Store.xcarchive`，Apple Distribution 签名 |
| 两份 App 验签 | `codesign --verify --deep --strict` 通过 |
| 商店导出 | `xcodebuild -exportArchive` 成功；手动选择 Application / Installer 发行身份 |
| 安装包 | `OpenBiliClaw.pkg`，271,672,569 字节；`pkgutil --check-signature` 通过 |
| 发行定价 | 免费下载，无内购；用户自行配置的第三方服务按其规则收费 |

安装包 SHA-256：`7d518181f35c3f0f5886af48f67c1a69569f470333daf42a30d828edeff29816`。

## 改名后的真实运行

直接启动签名 QA App，使用独立 Testing 配置与端口 `18702`，不是 Python 进程内的测试客户端。

- `/api/ping`、`/api/runtime-status`、`/api/qr-info`、`/web/`、`/m/`、共享手机连接 JS、英文语言目录、更新状态、自启动状态共 9 项真实 HTTP 请求均返回 200。
- 后端监听 `0.0.0.0`；通过实际局域网接口地址请求手机页面返回 200。此项仍为同机 LAN IP 请求，不代表跨机验收。
- 商店更新边界正确：`mac_app_store`、`app_store_managed`，手动更新 apply 返回 409。
- 私有随机端口 Ollama 的版本接口返回 `0.40.14`；原生控件、自检与两项许可证记录检查通过。
- 正常退出回收本轮记录的 6 个受管进程。

本次没有重复导入模型权重或新增 Tailnet 节点。此前真实模型生成、IPv6 与宿主异常退出结果见 [11 月 5 日实测](2026-11-05-mac-app-store.md)。

## 自动检查

- 本机相关测试：135 passed / 2 skipped；Ruff、MyPy（367 个源文件）、plist 与 Xcode 工程格式检查通过。
- 同一代码提交的 [Apple Silicon 沙盒归档及真实请求验证](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42267615013) 通过，包含配置保存后的双启动、私有 embedding 服务、IPv6 就绪和退出清理。
- 上一代码提交的 [完整 CI](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42039320595) 通过。
- 本机全量复跑在 `test_api_config_transactional.py::test_busy_dialogue_queues_config_and_latest_revision_wins` 的 1 秒配置请求等待处超时。停止检查时为 1 failed / 1,310 passed / 13 skipped（32m44s）；同一测试隔离复跑为 1 passed / 9 warnings（50.11s）。原因尚未确认，本次未改动该路由或测试阈值，也未完成全量复跑，不能记为全量通过。

## 提交状态与边界

App Store Connect 的独立 macOS 记录为 `6831698888`。独立记录使用 `OpenBiliClaw` 的保存被 Apple 拒绝，原因是同账号手机 App 已占用该名称；后改为 `OpenBiliClaw Hub`，副标题「你的 AI 内容中枢」，后台已显示保存成功。中文描述、关键词、支持/营销 URL、1.0.0 版本号、版权行和审核备注已保存。截图、隐私、分级和审核联系人仍在处理；**尚未完成上传或提交审核**。

新发行证书、私钥和安装包签名已经就绪，不能再把它们列为缺失条件。是否通过 Apple 的发行验证、上传、审核和实际公开上架，需要分别确认。

实际 Tailnet/公网跨机连接、登录项注册/撤销以及已识别 GPL/LGPL 依赖的完整分发与对应源码提供核对仍保持原有未完成边界；参见 [商店版说明](../mac-app-store.md#第三方许可待核对)。

本机持久证据放在 Xcode Archives 的 `2026-11-07/OpenBiliClaw-1.0.0-2-evidence/` 与 `OpenBiliClaw-1.0.0-2-Store-export/`，包含候选状态、真实请求报告、截图和安装包摘要；临时构建日志不作为唯一存档。
