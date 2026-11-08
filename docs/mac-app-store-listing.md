# Mac App Store 商店资料

名称、中文版本资料、截图、免费价格、年龄分级和隐私标签已保存。`1.0.0 (3)` 于 2026-11-08 02:55（北京时间）提交后被 Apple 标记为「二进制文件无效」。

截图进度（2026-11-08）：后台现保存 6 张新上传的 2880×1800 RGB 真实界面图，已重新载入确认数量、文件名、缩略图加载及顺序。内容依次为推荐、收藏、稍后再看、模型配置、平台源、手机二维码，随版本一起提交；旧低分辨率二维码已从当前版本移除，素材库原件保留。内容版权已按发行方的使用权确认填写并保存。详见[验证记录](verification/2026-11-07-mac-app-store-distribution.md#2026-11-08-内容版权确认与正式提审)。

- 平台：macOS；bundle ID：`com.openbiliclaw.backend`
- App Store Connect 记录：`6831698888`
- 商店名称：OpenBiliClaw Hub（已通过后台名称校验并保存）；安装后的 App 仍为 OpenBiliClaw。
- 初版 1.0.0，类别 Productivity。build 3 已失效，下一构建面向 Apple Silicon。
- 定价：免费下载，无 App 内购买；用户选择的外部模型服务可能自行收费。
- 发行地区：仅中国大陆，已保存并重新打开核验；不自动加入未来地区。此设置只针对独立 macOS 记录。
- 出口合规：build 3 已按标准加密算法、不在法国分发保存，缺项已消除。
- 中文副标题：你的 AI 内容中枢
- English subtitle: Your local AI discovery server
- 支持 URL：`https://github.com/whiteguo233/OpenBiliClaw/issues`
- 隐私政策 URL：`https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/privacy.md`
- Marketing URL：`https://whiteguo233.github.io/OpenBiliClaw/`

## 简体中文描述

让你的 Mac 成为自己的 AI 内容发现后端。OpenBiliClaw 将内容理解、兴趣画像与推荐运行在你控制的本机服务中，并让浏览器插件、手机网页版与配套手机 App 连接同一个后端。

从菜单栏打开 Web 界面，设置模型与内容来源，再通过连接二维码把手机接入。支持同一局域网访问，也可以配置自己的公网入口或 Tailnet 网络。

你可以使用包内 Ollama 在本地运行模型，或连接自己配置的兼容模型服务。模型权重按需下载；使用远端服务时，请同时了解对应服务的数据处理方式和费用。

商店版使用 macOS App Sandbox，数据保存在独立应用容器中。登录时启动是可选设置，退出 App 会停止它管理的后台服务。更新通过 Mac App Store 完成。

部分内容来源需要用户登录对应平台并安装配套浏览器扩展。来源可用性取决于平台、账号权限及网络环境。商店版不自动读取浏览器 Cookie 数据库，不支持外部开发者 CLI。

## English description

Run your own AI content discovery backend on your Mac. OpenBiliClaw brings content understanding, interest profiles and recommendations to a local service that connects your browser extension, mobile web browser and companion phone app.

Open the Web interface from the menu bar, configure your model and content sources, and connect your phone with a QR code. Use your local network, your own public endpoint or a configured Tailnet connection.

Use the bundled Ollama runtime for local models or connect a compatible model service of your choice. Model weights download when requested. Remote providers have their own data practices and may charge for usage.

The Mac App Store edition uses App Sandbox and stores data in its own application container. Starting at login is optional. Quitting the app stops the background services it manages. Updates arrive through the Mac App Store.

Some sources require your platform account and the companion browser extension. Availability depends on the platform, account permissions and network. The store edition does not automatically read browser cookie databases. External developer CLI integrations are not supported.

## 审核备注草案

This is a local backend application, not a hosted account service. Launch it and choose “Open Web & connection QR” in the window or menu bar. The bundled local Web UI opens in the user's browser. Initial setup can be inspected without logging into a content platform. A model must be configured for AI features; local model weights can be downloaded explicitly using the included Ollama runtime. No standalone applications or executable updates are downloaded at runtime.

The signed Python backend and native tools inherit the host's App Sandbox. The backend listens for incoming connections so the user's phone and extension can connect. The application does not install a system VPN or privileged daemon. Login startup is off by default and uses SMAppService only on user action. Closing the window keeps the visible menu-bar app running; choosing Quit stops its managed services.

## 提审状态与剩余边界

名称、免费价格、版权行、中国大陆供应范围、年龄分级、App Privacy、六张截图和审核联系人均已保存。内容版权字段依据发行方的使用权确认填写。build 3 的 Intel 发行包收到缺少 arm64 警告，随后审核提交转为「二进制文件无效」。实际 GPL/LGPL 依赖的商店分发条件和源码提供方式仍待独立核对。 [许可记录](mac-app-store.md#第三方许可待核对) · [隐私政策](privacy.md)
