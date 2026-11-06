# OpenBiliClaw 商店发行归档验证（2026-11-07）

当前候选为 build 3：已上传并选入版本草稿，未提交审核。下方保留 build 2 历史，最新进展见文末。

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

App Store Connect 的独立 macOS 记录为 `6831698888`。独立记录使用 `OpenBiliClaw` 的保存被 Apple 拒绝，原因是同账号手机 App 已占用该名称；后改为 `OpenBiliClaw Hub`，副标题「你的 AI 内容中枢」，后台已显示保存成功。中文描述、关键词、支持/营销 URL、1.0.0 版本号、版权行和审核备注已保存。中文截图已上传 1 张，审核联系人沿用现有手机 App；隐私政策 URL 与 16+ 分级（旧系统 17+）已保存。免费价格已选定，供应情况设置为 175 个地区。**Xcode 已完成上传，尚未提交审核**。

Xcode Organizer 已显示 `Upload completed with warnings`。警告为 Intel 归档不含 arm64，以及 PyObjC `_AppKit` / `_inlines` 扩展缺少 dSYM；这些警告未阻止上传。此前的手动导出包与这次 GUI 重新签名上传的安装包是不同文件：实际上传包为 271,701,862 字节，SHA-256 `7c0ed8a3a70f6038579dc8dbcc30d47de5020c9ded21066151b509b48ec4f0e8`，从上传暂存目录保留后再次验签通过，版本和构建号仍为 `1.0.0 (2)`。

App Store Connect 构建处理、构建选择、隐私标签发布和最终审核提交需继续确认。第三方内容版权声明已向发行方询问，尚未代填。

实际 Tailnet/公网跨机连接、登录项注册/撤销以及已识别 GPL/LGPL 依赖的完整分发与对应源码提供核对仍保持原有未完成边界；参见 [商店版说明](../mac-app-store.md#第三方许可待核对)。

本机持久证据放在 Xcode Archives 的 `2026-11-07/OpenBiliClaw-1.0.0-2-evidence/` 、`OpenBiliClaw-1.0.0-2-Store-export/` 与 `OpenBiliClaw-1.0.0-2-Xcode-upload/`，包含候选状态、真实请求报告、截图和安装包摘要；临时构建日志不作为唯一存档。

## build 3 许可证补漏

冻结 TOC 核对还发现 `pystray 0.19.5`。提交 `6c5e2b3b` 将其纳入签名前的许可证记录，并保留 `COPYING.LGPL` 等补充文本；新增回归测试验证 LGPL 补充不会遗漏。相关打包测试 130 passed / 2 skipped，Ruff 通过。`1.0.0 (3)` 发行归档已生成并通过深度严格验签，实际资源含三个依赖及 pystray 的 GPL/LGPL 两份正文，Xcode 已完成上传；冒烟检查同步改为三项及 LGPL 补充。

隐私标签已经通过后台发布：其他用户内容、浏览/搜索历史、用户与设备标识及产品交互，按 App 功能和内容个性化使用，关联身份，不用于广告追踪。第三方内容版权声明仍等待发行方确认。Apple 的标准加密文稿问卷在法国发行选项下要求法国加密声明批准文件；账户 DSA 身份也未设置。法国发行范围与 DSA 身份已询问发行方，未擅自代选。

### build 3 最终上传与草稿状态

- 实际上传安装包：271,693,812 字节；SHA-256 `aa40f733c334c1d4a0b1b3e1810aadd5ae4db742e42482c45403325a853bca7b`，Installer 签名复验通过，版本号保持 `1.0.0 (3)`。Xcode 上传完成，仍为同样的 Intel-only 与两个 PyObjC 扩展缺少 dSYM 的警告。
- App Store Connect 已处理 build 3，并选入 1.0.0 版本草稿保存；状态仍为「缺少出口合规证明」。未发出审核提交。
- 审核联系人与现有手机 App 完全一致，备注 883 字符、无需登录；重新打开页面核验后确认均已保存。隐私标签已发布；175 个地区的已保存价格逐项读取，全部为 0.00。法国/欧盟供应范围仍等待发行方选择，未按默认建议擅自缩减。
- 新签名 Intel QA App 使用端口 18703 的独立配置，9 项真实 HTTP、同机 LAN IP、私有 Ollama、商店更新边界、三个许可记录及 LGPL 补充、原生布局和 Tailnet 自检通过；累计记录的 13 个子进程均退出。没有新增跨机 Tailnet/公网验收。
- [Apple Silicon 同轮 QA](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42285317800) 在代码提交 `ee2989c6` 通过，包含真实 HTTP、双启动、IPv6 和退出清理；它是 ad-hoc QA，不能当作已上传的 ARM 发行包。此次上传仍为 Intel 包。
- 第一方 `6c5e2b3b` 源码归档与三项已识别 copyleft 上游源码已备份；PyPI sdist 校验发布哈希，pystray 0.19.5 无 sdist，改从上游版本 tag 归档，13 个 Python 文件与已安装 wheel 一致。材料不等于完整对应源码提供或商店许可兼容结论。初步 TOC 清点映射到 82 个 Python distribution；80 个在安装环境中含许可/声明文件，qrcode-terminal 与 rdt-cli 的已检查 wheel/sdist 未找到独立许可文件（元数据分别声明 MIT、Apache-2.0）。这个清点不表示 80 项文本已全部写入 App，完整分发核对仍待完成。

本轮持久材料：`2026-11-07/OpenBiliClaw-1.0.0-3-evidence/`、`OpenBiliClaw-1.0.0-3-Xcode-upload/` 和 `OpenBiliClaw-1.0.0-3-source-materials/`。发布仍需发行方的内容使用权声明、法国发行/加密材料选择、欧盟 DSA 身份，以及上述分发许可核对。
