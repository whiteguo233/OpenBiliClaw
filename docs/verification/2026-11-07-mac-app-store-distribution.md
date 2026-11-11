# OpenBiliClaw 商店发行归档验证（2026-11-07）

当前商店提交为 `1.0.0 (5)`，于 2026-11-10 00:08（北京时间）重新提审。App Store Connect 显示「等待审核」。[重新提审回执](#2026-11-10-build-5-重新提审)

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

本轮持久材料：`2026-11-07/OpenBiliClaw-1.0.0-3-evidence/`、`OpenBiliClaw-1.0.0-3-Xcode-upload/` 和 `OpenBiliClaw-1.0.0-3-source-materials/`。上述为地区选择前的历史状态，最新结果如下。

### 中国大陆发行与审核预检查

发行方明确要求提审并说明不在国外使用后，仅将本 macOS App 的供应范围收窄到中国大陆。重新载入供应情况页面核验为 1 个地区，中国大陆「App 发布时供应」，法国、美国等其余地区「未供应」；管理对话框确认没有自动加入未来地区。既有手机 App、其他 App 及账号级 DSA 身份均未更改。

build 3 的出口合规问卷选「与 Apple 操作系统加密并用的标准加密算法」，对应包内 TLS / WireGuard；法国发行选「否」。保存后构建行不再显示「缺少出口合规证明」。点击「添加以供审核」后，Apple 返回唯一缺项：「你必须在 App 信息中设置内容版权信息」。未把缺少字段的版本描述成已提交；内容使用权事实已再次向发行方询问。

本轮没有修改二进制或重跑构建测试，沿用上述签名安装包及已记录 QA。新增后台核验保存在 build 3 evidence 的 `asc-china-availability.json`、`asc-review-preflight-china.json`。完整开源分发许可核对仍保持前述未完成边界；法国材料和欧盟 DSA 不再是本次中国大陆候选的预检查缺项。

### 2026-11-08 真实使用截图

发行方要求增加高质量真实使用截图。已从 build 3 的独立签名 QA App 启动专用测试资料，实际请求本机后端并渲染包内 Web 资源；`app.js` 与 `app.css` 与候选 3 一致。通过已授权的 AppleScript 调用 Chrome 官方截图命令，使用应用实际支持的迁移偏好切换浅色主题并收起侧栏；没有改写界面内容、模拟 HTTP 响应或编辑截图像素。

已检查 6 张 2880×1800 RGB PNG，分别为推荐首页、收藏、模型设置、平台源、稍后再看和手机连接二维码。收藏是本次通过正常 API 保存的 6 个中性公开内容，稍后再看为本次本地保存的 34 个公开视频；没有执行平台账号同步，也没有将私人画像、历史对话或账户凭据截图。素材、尺寸校验和 SHA-256 清单位于 Xcode Archives 下 `2026-11-07/OpenBiliClaw-1.0.0-3-evidence/store-screenshots/selected/manifest.json`。先前空封面和重复首页的草稿不属于这 6 张。

当前网络中，未缓存 CDN 封面请求会被公开地址校验拒绝；另发现已消费且未保存的缓存会在启动清理时删除。为截图使用实际公开 CDN 图片，通过正常迁移恢复缓存，并用正常收藏/稍后再看操作保留测试内容。最后 8 个可见推荐、4 个惊喜推荐及 6 个收藏的图片代理请求共 18 次均为 HTTP 200，且截图中封面可见。这不表示未缓存请求的问题已修复，也不代表新的跨节点、公网或完整端到端验收；本轮未修改后端、UI 或发行二进制。一次本地 7B 模型探测等待 90 秒超时，未记录为通过，模型截图保留真实的「尚未测试」状态。

**截图上传完成。** Chrome 控制连接仍不可用，系统仍检测到锁屏；本轮恢复了已授权的 Chrome AppleScript 网页接口，使用页面真实文件上传控件依次上传并等待 Apple 完成处理。手机二维码先在真实 QA App 中打开，再用 Chrome 官方 AppleScript `save ... as "single file"` 导出该实际界面的原生归档，并由 Chrome 官方截图命令生成 PNG；没有改写界面、业务数据或像素，也未改用独立 Playwright 驱动。Apple Vision 从 PNG 识别出与界面一致的局域网手机地址，同机真实 HTTP 请求手机页面返回 200。此项只覆盖二维码图片识别和同机 LAN 请求，不能代替手机或跨节点测试。

最终后台顺序为推荐、收藏、稍后再看、模型、平台源、手机连接，6 张均为本轮新上传的 2880×1800 图片。旧 1440×900 二维码已从此版本移除，Apple 明确提示素材库原件保留；没有删除素材库内容。重新载入版本页后确认 `6/10`、六个文件名、顺序、缩略图均已加载，保存按钮没有未保存更改。当时再次点击「添加以供审核」，Apple 仅返回内容版权缺项；随后完成发行方事实确认及正式提审，结果见下一节。中国大陆供应范围、加密声明、发布方式和 iOS App 设置保持原有配置。

新增本机证据为 `store-screenshots/asc-upload-verification.json`、`phone-qr-verification.json` 和 evidence 根目录的 `asc-review-preflight-after-screenshots.json`。原生 App Store 页面归档未完整保留其动态样式，无法作为有效后台页面截图，已移除失败图；没有以该图替代实际后台核验结果。

文档收尾验证：6 张 PNG 解码、尺寸和 RGB 模式检查通过，摘要已写入素材清单；`git diff --check` 通过。打包相关 3 个测试文件与 Mac Store 构建、运行时 2 个测试文件合计 136 passed / 2 skipped；没有将这些回归测试描述为完整端到端验收。

### 2026-11-08 内容版权确认与正式提审

发行方明确确认具备第三方内容所需使用权并要求代办后，选择 `USES_THIRD_PARTY_CONTENT`，完成并保存内容版权信息；重新载入 App 信息页，仍显示「是，此 App 拥有对其第三方内容的必要权利」。该声明依据发行方确认填写，不作为本轮独立法律审查或全部开源分发条件已核对的结论。

版本页重新载入后六张截图完整，「添加以供审核」通过，草稿提交只包含 `macOS App 1.0.0`、构建 `1.0.0 (3)`。随后点击最终「提交以供审核」，Apple 返回「已提交 1 个项目」，版本侧栏变为「正在等待审核」。进入 App 审核详情后，唯一项目显示「等待审核」，提交日期为 **2026-11-08 02:55（北京时间）**，提交 ID 为 `3f87d5f8-bab3-4a64-9a4a-7d2de420dbb1`。

该提交使用既有 build 3 安装包与六张真实截图。免费、中国大陆供应范围、隐私标签和原有自动发布设置已保存；当时显示「等待审核」。

本机证据：build 3 evidence 下的 `asc-content-rights-confirmed.json` 和 `asc-review-submitted.json`，分别记录声明重新载入核验，以及 Apple 最终提交回执、等待审核状态、构建与提交详情链接。

### 2026-11-09 Apple Silicon build 4

- Apple 在 2026-11-08 02:57（北京时间）将 build 3 标记为「二进制文件无效」。该构建的上传警告 `ITMS-91167` 报告缺少 arm64。审核页面未列出更详细的原因。
- 首次 Apple Silicon 归档的全量架构检查发现 32 个仅有 Intel 切片的 Ollama 动态库。官方 v0.40.14 的 arm64 runner 静态链接这些组件。修复在签名前筛除这批 Intel 库、提取通用二进制的 arm64 切片，并拒绝其余架构不匹配的代码。
- GitHub Actions 以提交 `593db6e4` 生成 `1.0.0 (4)`，9 项真实 HTTP、同机局域网的手机页面请求、内置 Ollama 384 维向量请求、Tailnet 二进制自检、IPv6 重启和退出清理均通过。[GitHub 构建及测试](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42500305550)
- GitHub 归档按发布的 SHA-256 校验后，用本机已有的 Apple Distribution 证书重新签名，并用 Mac App Store Installer 证书生成安装包。解包复验了全部 241 个 Mach-O 的 arm64 切片、App 签名、安装包签名、沙盒权限和发行描述文件。签名安装包为 220,018,915 字节，SHA-256 `333d322208d8a1c590dcbfde729d431c7a28d80a0c3b68719c2fbafcbbfa24ea`。本机为 Intel，运行测试使用 GitHub 的 Apple Silicon 沙盒归档。
- 打包相关 pytest 为 138 passed / 2 skipped，修改文件的 Ruff 与 `git diff --check` 通过。
- Xcode 命令行上传报 `Failed to Use Accounts`，当时 App Store Connect 的上传列表只有 build 2 和 build 3。后续上传的是 build 5。

本机归档目录为 `2026-11-09/OpenBiliClaw-1.0.0-4-Store.xcarchive`，安装包和验证记录分别保存在同日的 `OpenBiliClaw-1.0.0-4-Store-export/` 和 `OpenBiliClaw-1.0.0-4-evidence/`。

### 2026-11-09 基于最新主干的 build 5

- 基线为主干 `8b760446`。首轮 ARM64 沙盒验证发现首启的免费组件准备请求被 degraded 路由守卫拦为 503。回归复现后补齐恢复入口，定向打包、语言和运行时测试 285 passed / 2 skipped，degraded 路由组 22 passed。Ruff、Swift 类型检查和源码 MyPy 通过。
- 发行源码为 `7333a6d7`。[GitHub Actions](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42522818585) 完成了沙盒启动、9 项 HTTP、免费组件准备、同机 LAN 手机网页、内置 Ollama 384 维向量、IPv6 双启动和退出清理。测试归档按 GitHub SHA-256 校验。
- 最终发行包换入同一主干对应的新版 ARM64 Rust 组件。私有构建完成原生编译和策略自检，组件原始签名清单、Core/Rust 提交及 SHA-256 另行核验。打包后解包确认 244 个 Mach-O 均为 arm64，App 与安装包签名、发行描述文件和 helper 沙盒继承均通过。GitHub 完整 App 运行测试使用固定的公开 SDK，最新私有 SDK 通过独立原生构建和最终安装包验签。
- Apple 官方命令行于北京时间 05:17 完成上传。实际上传包为 265,064,526 字节，SHA-256 `946000f4146354c6b169d40f34077ce5fc0cb1004ecef1552d539ce7bbe5f5eb`，交付 ID `0722aa10-587a-4341-b29e-5a5ccc76f7ba`。同一交付的 Apple 状态为 `VALID`、`VALID_BINARY`、`APP_STORE_ELIGIBLE`，构建号为 5。

本机证据保存在 `2026-11-09/OpenBiliClaw-1.0.0-5-evidence/`，签名安装包位于 `OpenBiliClaw-1.0.0-5-Store-export/`。

### 2026-11-10 build 5 重新提审

在 macOS 1.0.0 版本中换入 Apple Silicon build 5，按包内 TLS / WireGuard 标准加密算法和中国大陆发行范围填写出口合规问卷。Apple 清除合规缺项后，通过「更新审核」及「重新提交至 App 审核」提交。

Apple 在审核详情中显示提交时间为 **2026-11-10 00:08（北京时间）**，提交 ID 为 `3f87d5f8-bab3-4a64-9a4a-7d2de420dbb1`。唯一审核项目为 macOS App `1.0.0 (5)`，状态为「等待审核」。重新打开分发页后，版本仍显示「正在等待审核」。[App Store Connect 版本页](https://appstoreconnect.apple.com/apps/6831698888/distribution/macos/version/inflight)

本机 build 5 evidence 目录保存了 `asc-review-submitted.json` 和 `asc-review-build5.jpg`。
