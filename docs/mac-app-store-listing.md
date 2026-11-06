# Mac App Store 文案草稿

此文件记录商店文案与实际填写进度；名称和中文版本资料已保存，尚未提交审核。年龄分级和隐私声明以后台实际保存结果为准。

- 平台：macOS；bundle ID：`com.openbiliclaw.backend`
- App Store Connect 记录：`6831698888`
- 商店名称：OpenBiliClaw Hub（已通过后台名称校验并保存）；安装后的 App 仍为 OpenBiliClaw。
- 初版：1.0.0，当前候选 build 2；类别：Productivity
- 定价：免费下载，无 App 内购买；用户选择的外部模型服务可能自行收费。
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

## 后台仍需确认

上述 URL 沿用项目既有公开入口。名称已保存为 OpenBiliClaw Hub；免费价格、版权主体、分发区域、年龄分级、App Privacy、截图、Apple Silicon 的发行包和审核联系人，均需按真实账号与最终发行结果填写并保存。实际 GPL/LGPL 依赖的商店分发条件和源码提供方式尚待核对，见[许可记录](mac-app-store.md#第三方许可待核对)。已有隐私政策：[`docs/privacy.md`](privacy.md)。不要把本地优先等同于所有配置下的数据都不会离开设备。
