# Mac App Store 桌面版

商店版是独立的 **OpenBiliClaw** macOS App，bundle ID 为 `com.openbiliclaw.backend`，与手机 App `com.openbiliclaw.mobile` 分开。`1.0.0 (3)` 已于 **2026-11-08 02:55（北京时间）提交 Apple 审核，当前状态为「等待审核」**，尚未上架。原有 Developer ID / 公证 DMG 继续使用 `packaging/build.py`，不切换为商店版。

安装后的桌面 App 名称为 `OpenBiliClaw`，商店名称为 `OpenBiliClaw Hub`，中文副标题为「你的 AI 内容中枢」。同账号手机 App 已占用 `OpenBiliClaw`，Apple 后台拒绝独立 macOS 记录同名，现名称已通过校验并保存。发行定价为免费，不设置 App 内购买；用户自行配置的第三方模型服务费用由对应服务决定。

本次按发行方要求仅供应中国大陆，已重新打开后台核验：1 个供应地区，中国大陆为「App 发布时供应」，法国、美国及其余地区均为「未供应」，不自动加入未来地区。build 3 的加密问卷按标准算法（包内 TLS / WireGuard）及不在法国分发填写，出口合规缺项已消除。发行方确认具备第三方内容所需使用权后，内容版权选择「是」并保存、重新载入核验；随后完成「添加以供审核」和最终「提交以供审核」。手机 App 的地区设置和账号级欧盟 DSA 声明保持原有配置。

2026-11-08 已上传并重新载入核验 6 张 2880×1800 RGB 真实使用截图，顺序为推荐、收藏、稍后再看、模型配置、平台源、手机连接二维码，随 build 3 一并提交。图片来自 build 3 独立 QA 实例及其原版 Web 资源，已逐张检查；旧低分辨率二维码已从当前版本移除，原件仍在素材库。新二维码图片识别得到实际局域网地址，同机访问手机网页返回 HTTP 200。素材与清单位于 build 3 evidence 的 `store-screenshots/selected/`，提审结果见[发行验证记录](verification/2026-11-07-mac-app-store-distribution.md#2026-11-08-内容版权确认与正式提审)。

## 运行与数据

```text
Mac App Store → Xcode 原生 App（App Sandbox、网络入站/出站）
  ├─ SMAppService.mainApp ← 用户在窗口中勾选「登录时启动」
  └─ lifetime pipe → 冻结 Python 后端（继承宿主沙盒）
       ├─ full / discovery / recommendation / image 子进程
       ├─ 包内 Ollama → 容器内模型；首次下载由设置页明确触发
       └─ 包内 userspace Tailnet → 容器内节点状态
     HTTP 0.0.0.0 → Web / 插件 / 手机网页版 / 原生手机 App
```

- macOS 14 及以上；Xcode 原生编译宿主，PyInstaller 冻结同架构 Python 依赖。本机实测 Intel，独立 CI 已完成 Apple Silicon 的沙盒归档与真实 HTTP 验证；两个单架构包都不能称为 Universal。
- 配置、数据、模型及日志位于容器的 `Library/Application Support/OpenBiliClaw`；不自动读取或搬动 `~/OpenBiliClaw`、浏览器 Cookie 数据库或旧 DMG 的数据。迁移使用现有 Web 导出/上传导入流程。不要在路径设置中指定容器外目录。
- Python、Ollama、Tailnet 均在 App 内，不在运行时安装 pip、Homebrew、Tailscale 或其他程序。模型权重是数据；商店版不运行首次启动的自动模型拉取，用户在 Web 设置中决定是否下载。
- 关闭窗口保留菜单栏服务；「停止后端」或退出 App 会请求后端停止，并清理其拥有的 workers、Ollama 与 Tailnet。宿主意外结束也会关闭 lifetime pipe。不会终止用户另行启动的 Ollama。
- 对话模型切换到另一 Ollama 地址后，重启仍优先启动容器内的本地 embedding 服务；原生窗口按实际监听地址探测就绪，支持显式 IPv6 绑定。
- 局域网二维码仍从真实接口地址生成；系统 Tailscale 与内置 Tailnet、公网 URL 继续走同一 `/api/qr-info` 契约。联网、macOS 本地网络授权、远端实际可达性仍是扫码成功的前提。
- 登录启动由原生窗口中的 `SMAppService.mainApp` 控制，不从旧 `[autostart].enabled` 自动注册；Web / 插件提示在 Mac App 中设置。首次安装不会自动添加登录项。
- 更新完全由 Mac App Store 管理。`install_mode=mac_app_store`，`reason=app_store_managed`；不轮询 GitHub 安装器、不调用 git/uv/pip 更新，手动 apply 返回 409。源码、Docker、普通 DMG 的更新机制保持原样。
- 读取任意本机目录、自动读取浏览器 Cookie、调用外部开发者 CLI 等高级用法不属于商店版保证范围；请使用扩展授权同步、内置 Ollama或远端模型 API。需要这些本机能力的用户继续使用 DMG/源码版。

## 构建

```bash
uv venv --python 3.12
uv pip install --python .venv/bin/python -e '.[dev,packaging]'
.venv/bin/python scripts/generate_tailnet_notices.py --prefetch
# 解压官方 Ollama.app（将版本和 SHA-256 固定在构建环境中）
.venv/bin/python packaging/macos_store/build.py \
  --ollama-bin /path/to/Ollama.app/Contents/Resources/ollama \
  --version 1.0.0 --build-number 1 \
  --team YOUR_TEAM_ID --signing-identity 'Apple Distribution: Your Name (YOUR_TEAM_ID)'
```

构建要求完整官方 Ollama 运行时（含 runner 和动态库），并强制编译包内 Tailnet。输出 `dist/mac-app-store/OpenBiliClawStore.xcarchive`，Xcode 工程位于 `packaging/macos_store/OpenBiliClawStore.xcodeproj`。

本机 QA 可以显式加 `--local` 使用 Development / Developer ID 签名；**这不是可上传商店的发行归档**。`--provisioning-profile NAME_OR_UUID` 可指定已安装的手动 distribution profile。`--reuse-backend` 只适用于宿主 UI 改动或相同源代码/依赖的重试；改了 Python、Web 或语言包必须重新冻结后端。

App 宿主只有 sandbox、network.client、network.server 三项权限。可执行 helper 只有 sandbox 与 inherit；动态库单独签名，不加 helper 权限。所有资源修改均在 Xcode 最终签名前完成，Mach-O 不作为 Resources 的普通文件打包。最终运行 `codesign --verify --deep --strict`。

## 提审与人工账号步骤

1. 在 Apple Developer 为 `com.openbiliclaw.backend` 创建 macOS App 标识，准备 Mac App Store 发行证书及对应私钥、distribution provisioning profile。
2. 在 App Store Connect 创建独立的 macOS App，名称可用性以后台为准；不要覆盖手机 App 或其他既有 App。初版营销版本建议 `1.0.0`，构建号从 `1` 起。
3. 用发行身份归档；若账号要求手动 profile，在 Xcode Signing & Capabilities 选择该 profile。通过 Organizer 的 Validate App 检查，导出或上传 App Store Connect。`ExportOptions.plist` 是导出模板；本机 QA 归档不能直接复用为发行包。
4. 填写描述、截图、年龄分级、支持/隐私 URL、App Privacy 和模型/第三方服务说明；根据真实数据流填写，不能把发往用户配置模型服务的数据声明为完全不出设备。
   同时核对实际打包依赖的分发许可与对应源码提供方式，见下方「第三方许可待核对」。
5. 审核备注说明本机后端用途、菜单栏入口、关闭窗口与退出的区别、默认不加登录项、手机与插件连接方式，以及无需配置第三方账号即可检查的本机启动流程。
6. 在可访问的测试机器完成联网授权、可选登录项的注册/撤销、真实双端 Tailnet/公网访问和 arm64 验收后再提交；最终结果以 Apple 审核为准。

现有 Apple Developer Program 会员即可覆盖 macOS；Developer ID 证书用于商店外分发，不能替代商店发行证书。账号、私钥和真实审核提交状态以实时检查为准。

参考：[辅助进程嵌入规范](https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app)、[代码与资源位置](https://developer.apple.com/documentation/bundleresources/placing-content-in-a-bundle)、[入站网络权限](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.network.server)、[审核指南 2.4.5](https://developer.apple.com/app-store/review/guidelines/#hardware-compatibility)。

## 验证与材料

- [真实环境验收记录](verification/2026-11-05-mac-app-store.md)
- [OpenBiliClaw 1.0.0 (3) 上传、签名与版本草稿状态](verification/2026-11-07-mac-app-store-distribution.md)
- [中英文商店文案与审核备注草稿](mac-app-store-listing.md)
- `.github/workflows/build-mac-app-store.yml`：只读的 arm64 QA 构建与真实 HTTP 冒烟，不含 Apple 发布凭据，也不上传商店。

本机实测入口：

```bash
.venv/bin/python packaging/macos_store/smoke.py \
  "dist/mac-app-store/OpenBiliClawStore.xcarchive/Products/Applications/OpenBiliClaw.app" \
  --output /tmp/obc-store-smoke
# 可选 --models-root ~/.ollama/models：经 HTTP 将已有公开权重导入隔离容器，验证真实推理。
# --crash-host：验证宿主异常退出后的 EOF 清理。
.venv/bin/python packaging/macos_store/restart_smoke.py \
  "dist/mac-app-store/OpenBiliClawStore.xcarchive/Products/Applications/OpenBiliClaw.app" \
  --output /tmp/obc-store-restart
# 同一隔离配置两次启动，验证独立 embedding 端口、IPv6 就绪和两次退出清理。
```

## 第三方许可待核对

本轮实际构建环境中的 `bilibili-api-python 17.4.2` 为 `GPL-3.0-or-later`，`browser-cookie3 0.20.1` 随包许可证为 LGPLv3；冻结 TOC 还包含 `pystray 0.19.5`（LGPLv3）。项目自身的 MIT 许可证不会替代这些上游许可；现有 `THIRD_PARTY_NOTICES.md` 主要记录 Tailnet 的 Go 依赖，不能视为 Python 依赖已经核对完毕。

构建器在最终签名前把上述三个依赖的许可证原文、实际版本及项目来源写入 `Contents/Resources/ThirdPartyLicenses`，复用冻结 payload 时也会更新；缺少许可证原文即构建失败。扫描包括 `COPYING.LGPL` 等 `COPYING.*` / `LICENSE*` 补充文本，避免只保留通用 GPL 正文而漏掉 LGPL 补充条款。build 2 上传后核对发现 pystray 原文遗漏，现已将补齐后的 build 3 上传并选入版本草稿，build 2 不再用于提审。这个有限记录不等于完整 SBOM、对应源码提供或已确认商店兼容。

正式分发前还需确认这些组件的分发条件、对应源码的提供方式，以及与适用商店协议的兼容性。Apple 的[标准 EULA](https://www.apple.com/legal/internet-services/itunes/dev/stdeula/)包含开源组件许可的例外，也允许自定义 EULA；FSF 曾针对[2010 年的 App Store 条款](https://www.fsf.org/blogs/licensing/more-about-the-app-store-gpl-enforcement)提出冲突意见。旧案例不足以单独判定当前 macOS 候选能否分发，本次没有取得上游额外授权，也没有完成这一许可结论。不要把技术验证通过解释为许可问题已经解决；候选保持未提交状态，不擅自修改上游许可或删去核心 B 站功能。
