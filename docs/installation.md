# 安装与部署

[返回 README](../README.md#快速开始) · [English](installation.en.md) · [常见问题](faq.md)

第一次使用，按 **桌面后端 → 浏览器插件 → 模型与来源 → 初始化** 完成设置。本指南也保留 AI 助手、安装脚本、Docker 与源码部署路径。

## 选择安装方式

| 你的情况 | 安装路线 |
|---|---|
| macOS / Windows，先体验产品 | [桌面安装包](#desktop)（实验性预发布） |
| 想让 AI 助手处理环境或修改源码 | [AI 一句话部署](#ai-deploy) |
| Linux / WSL2 或习惯终端 | [安装脚本](#script-install) |
| 已有 Docker 环境 | [Docker 部署](#docker) |
| 参与开发、调试来源 | [手动安装](#manual) |

## 开始前准备

- **一台持续运行后端的电脑**；手机和浏览器插件连接它，不会各自运行一套推荐后端。
- **一个可用的 LLM 服务**，用于画像、推荐理由和对话。准备对应服务的 API Key / endpoint / model；费用按所选服务计算。
- **独立的 embedding 服务**，用于内容向量与相似度。默认推荐本地 Ollama `bge-m3`，也可明确选择远程服务。它不会自动跟随聊天模型配置。
- **至少一个可返回画像信号的来源**，以及按该来源要求准备的登录浏览器或公开用户名。

桌面完整版只预置 embedding 模型。若要完全使用本地模型，还需自行配置满足硬件条件的聊天 LLM，见 [本地 LLM 硬件要求](agent-install.md)。

<a id="desktop"></a>

## 1. 安装桌面后端

从 [Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) 下载对应系统的安装包：

- **macOS**：Apple 芯片选择 `OpenBiliClaw-macos-v*-arm64.dmg`；Intel 选择 `OpenBiliClaw-macos-v*-x64.dmg`（如发布页提供）。打开 DMG 后，双击 `安装并启动 Install OpenBiliClaw.command` 完成安装并启动。
- **Windows**：下载 `OpenBiliClaw-windows-*-Setup.exe`，双击安装，保留勾选「Launch OpenBiliClaw」，再点「完成」。

**安装包选择。** 桌面包自带 Ollama 运行时。**精简版**首启下载 `bge-m3`；**`-with-embedding` 完整版**预置约 1.1GB 的 `bge-m3`，免去 embedding 模型下载。两者都仍需单独配置可用的 LLM；内置向量模型不等于内置聊天模型或全部离线。

启动后常驻 **macOS 菜单栏 / Windows 系统托盘**，右键可「打开 Web 界面 / 查看运行日志 / 退出」。

> ⚠️ **首次启动：macOS 安全阻挡与 Windows SmartScreen**：
> - 当前 Release 是 ad-hoc signed、未 notarized。首次打开安装助手或应用时如果提示“无法验证开发者”或“未经安全验证”，请右键 / Control-click 对应项目 →「打开」→ 在弹窗里再点「打开」；也可以到「系统设置 → 隐私与安全性」点击「仍要打开」。
> - 如果提示“`OpenBiliClaw.app` 已损坏，无法打开。您应该将它移到废纸篓”，通常是下载隔离属性导致。确认包来自本项目 Releases 后运行：
>
>   ```bash
>   APP="/Applications/OpenBiliClaw.app"
>   xattr -dr com.apple.quarantine "$APP"
>   ```
>
>   然后再次打开应用。
> - **Windows**：SmartScreen 弹窗点「更多信息 → 仍要运行」。
>
> 这是**实验性预发布**：未签名、随后端版本滚动更新，适合只想最快试用、不碰命令行的人。要二次开发 / 改源码请看下方 [AI 一句话部署](#ai-deploy)。

<a id="mirrors"></a>

> 🇨🇳 国内网络下载时，可使用 [123 云盘国内下载（v0.3.221 历史镜像）](https://4001474255.share.123pan.cn/123pan/IxbZMh-90KO3)；该分享保留 7 个 v0.3.221 安装包，不代表当前最新版本；下载前请与 GitHub Release 核对版本。插件、普通 Windows 安装包与源码也可从上面的 GitHub / [Gitee v0.3.221 发行版](https://gitee.com/whiteguo233/openbiliclaw/releases/tag/openbiliclaw-v0.3.221) 获取。

<details>
<summary>安装器的升级行为与 Windows 静默安装</summary>

- **macOS**：从发布页下载与你的 Mac 匹配的 DMG：Apple 芯片用 `OpenBiliClaw-macos-v*-arm64.dmg`；Intel 用 `OpenBiliClaw-macos-v*-x64.dmg`（如发布页提供）。打开后推荐双击 `安装并启动 Install OpenBiliClaw.command`：它会校验新包、退出旧实例、原子替换「应用程序」中的 app，再启动刚安装的版本；传统拖拽仍可用，但升级时需先退出旧版并在替换后手动重开。
- **Windows**：下载 `OpenBiliClaw-windows-*-Setup.exe`，双击安装。向导最后一页提供默认勾选的「Launch OpenBiliClaw」复选框，点「完成」才会启动刚安装的新版本（升级时安装器会先结束无互斥体的旧实例；若运行中的是已带 AppMutex 的新版应用，安装/卸载会先弹「OpenBiliClaw 正在运行」提示，退出应用后点 OK 自动重检，正常点「完成」即完成新旧交接，取消勾选则不启动）；`/SILENT` / `/VERYSILENT` 静默安装没有向导界面，安装成功后仍会自动启动新版本；但应用仍在运行时静默安装（配合 `/SUPPRESSMSGBOXES`）会按取消处理，需先退出应用。

</details>

<details>
<summary>内置依赖、可选 Tailnet 与 macOS 兼容性</summary>

安装包也内置默认内容源依赖，包括 X 的 `twitter-cli`、Reddit 的 `rdt-cli`，以及默认关闭的应用内 Tailnet helper。后者让电脑端应用自己加入用户的 tailnet，不安装系统 Tailscale。请在电脑本机的桌面 Web 或浏览器插件「设置 → 通用」中开启 Tailnet，选择网页登录、Auth Key 或 OAuth Client Secret 入网并完整重启；OAuth 方式还需填写该 OAuth Client 已获准使用的设备 tag。Reddit rdt 命令后端会优先使用已连接插件同步的 `reddit_session`，插件不可用时可手动运行 `rdt login`，未登录会 fallback 插件。

macOS 主应用继续以 10.15+ 为兼容目标；只有 Go 1.26.6 构建的可选 Tailnet helper 实测要求
macOS 12+。10.15 / 11 会只降级远程 Tailnet，本机应用功能仍照常。

</details>

<details>
<summary>数据目录、旧安装迁移与配置恢复</summary>

数据与 AI / 脚本安装复用同一个目录：`~/OpenBiliClaw`（macOS / Linux）/ `%USERPROFILE%\OpenBiliClaw`（Windows），升级或卸载不会动它；旧安装包曾写入的 `~/Library/Application Support/OpenBiliClaw` / `%LOCALAPPDATA%\OpenBiliClaw` 会在新版本首次启动时非覆盖拷贝回来。若 `config.toml` / `config.local.toml` 损坏导致启动失败，桌面包会把坏文件备份为 `*.invalid` 并重新生成默认配置，随后打开 `/setup/` 重新初始化；`data/` 不会被删除。

</details>

<details>
<summary>聚合发布页与 channel 规则</summary>

`openbiliclaw-v*` 聚合发布页会同步展示：

- 当前后端源码 tag：`backend-v*`
- 当前插件 release：`extension-v*`，并附 `openbiliclaw-extension-v*.zip` / `openbiliclaw-extension-v*-firefox.zip`（Firefox 临时调试）；启用 AMO signing 时还会附 `openbiliclaw-extension-v*-firefox.xpi`（Firefox 正式安装）
- 当前桌面安装包 release：`desktop-v*`，同版本桌面 channel 完成后会附可用的 `.dmg` / `.exe`；缺失 channel 显示未发布，不回填上一版资产

</details>

## 2. 安装浏览器插件

插件负责浏览器内的连接与交互：它会在受支持站点显示侧边栏、采集你的反馈，并承接知乎、Reddit、Linux.do、V2EX、微博等登录态只读任务。Linux.do、V2EX 与微博的任务 tab 和普通行为采集隔离；微博公开 discovery 由后端独立完成，个人初始化才使用微博 host permission 和同源任务桥。

插件基于 Manifest V3，支持所有兼容 Chrome 插件的浏览器，包括 **Chrome、Edge、Brave、Arc、Vivaldi、Opera** 等；另提供 **Safari（macOS）** 构建，Release 自动附带 `openbiliclaw-extension-v*-safari.dmg`（配置 Apple 凭据时为 Developer ID 签名 + 公证；未配置时为 ad-hoc 实验包，需在 Safari 开启「允许未签名扩展」），也可本地经 Apple `safari-web-extension-converter` 转成 Xcode 工程后安装（详见 [Safari 构建文档](safari-extension-build.md)）。

**省事方式 · Chrome 应用商店一键安装**（安装后由浏览器自动更新，适合不想手动升级的人；缺点是版本可能滞后于 Releases）：

> 👉 **[在 Chrome 应用商店安装 OpenBiliClaw](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg)** —— 打开后点「添加至 Chrome」即可。

**手动安装 · 从 Latest Release 聚合页下载最新可用构建**（拿到最新功能与修复 —— Chrome 应用商店受审核排期影响，版本通常会滞后几天到一两周）：

1. 打开 [OpenBiliClaw Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest)，也就是最新 `openbiliclaw-v*` 用户下载聚合页
2. Chrome / Edge / Brave 下载 `openbiliclaw-extension-v*.zip`；Firefox 若 release 提供 `openbiliclaw-extension-v*-firefox.xpi` 就直接安装，否则下载 `openbiliclaw-extension-v*-firefox.zip` 并按下方 `about:debugging` 临时加载；Safari（macOS）下载 `openbiliclaw-extension-v*-safari.dmg`，打开后首次运行 App，再到 Safari 设置 → 扩展里勾选 OpenBiliClaw
3. 打开扩展管理页面（Chrome：`chrome://extensions/` · Edge：`edge://extensions/` · Brave：`brave://extensions/`），开启右上角「开发者模式」
4. Chrome / Edge / Brave 将下载的 `.zip` 文件拖入页面安装；Firefox 的 `.xpi` 可直接打开确认安装，临时 zip 需要先解压再加载 `manifest.json`

插件更新取决于安装渠道：Chrome Web Store / Edge Add-ons，以及审核通过后的 Firefox AMO 上架版由浏览器自动更新；从 GitHub Release 下载的 Chrome zip / Firefox signed XPI / Firefox 临时 zip / Safari dmg、开发者模式加载或 Firefox 临时加载的用户，需要下载新版安装包并按同样方式重新加载。Firefox AMO 上架审核是异步的，listed 版本公开前优先使用 Release 已签名的 `*-firefox.xpi`；未提供 XPI 时再用 `*-firefox.zip` 临时加载；审核通过后由 Firefox 自动更新。后端设置里的“自动更新”开关只更新本地后端源码，不会更新浏览器插件。

<a id="firefox"></a>

<details>
<summary>Firefox 用户：正式安装与临时调试（Firefox 140+）</summary>

Firefox 用 `sidebar_action` 而不是 Chrome 的 `sidePanel`，所以 release 会提供独立产物：

- `openbiliclaw-extension-v*-firefox.xpi`：Mozilla AMO unlisted 签名后的正式安装包；仅在发布环境启用 AMO signing 且凭据可用时生成，普通 Firefox Release / Beta 可以直接安装。
- `openbiliclaw-extension-v*-firefox.zip`：未签名开发包，只用于 `about:debugging` 临时加载或 AMO 签名输入。普通 Firefox 直接安装它会提示“未通过验证 / could not be verified”。

临时调试或源码构建时使用：

```bash
unzip openbiliclaw-extension-v*-firefox.zip -d openbiliclaw-firefox

# 或从源码构建
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw/extension
npm install
npm run build:firefox          # 产出 dist-firefox/
npm run package:firefox        # 额外打成未签名 openbiliclaw-extension-v*-firefox.zip
# AMO 凭据配置后可签名成正式安装包：
# AMO_JWT_ISSUER=... AMO_JWT_SECRET=... npm run sign:firefox:only
```

加载方式：

1. 打开 `about:debugging#/runtime/this-firefox`
2. 点「Load Temporary Add-on…」
3. 选解压目录里的 `manifest.json`（或源码构建后的 `extension/dist-firefox/manifest.json`）

注意：Firefox 临时加载在浏览器重启后会失效；如果 release 提供已签名 `.xpi`，普通用户应优先使用 `.xpi`。

</details>

## 3. 配置模型与内容来源

打开 `http://127.0.0.1:8420/setup/`，选择 LLM 服务并填写凭据和模型，再确认独立 embedding 服务可用。桌面精简版首次下载模型需要联网；完整版省去这一步的模型下载，但仍需配置 LLM。

默认登录 [B 站](https://www.bilibili.com) 并勾选 B 站来源即可生成第一版画像和推荐；如果不想接 B 站，也可以改勾已登录的小红书 / 抖音 / YouTube / X / 知乎 / Reddit / [Linux.do](https://linux.do) / [V2EX](https://www.v2ex.com)，或选择 Bangumi / GitHub 并填写公开用户名。至少保留一个能拉到画像信号的来源；未填身份的 Bangumi / GitHub 仍可公开 discovery，但不能单独完成画像初始化。

只勾选你愿意用于初始化的来源。默认启用 B 站，其余来源在向导或设置里显式开启；平台的公开发现能力与个人画像初始化能力并不相同，详见[来源登录与限制](#source-access)。

## 4. 完成初始化，查看第一批推荐

在设置向导里检查模型连接和来源状态，再点击「开始初始化」。系统会读取所选来源的信号、生成画像并开始发现内容；所需时间取决于模型、网络与来源数据量。完成后在 `/web` 查看推荐及理由，留下「喜欢 / 不感兴趣」或聊天反馈，帮助后续推荐调整。

如果模型检查不通过，先修正对应服务的 Key / endpoint / model 或 embedding 连接；不要把未完成初始化当成安装成功。AI / 脚本部署会在服务检查通过后自动运行 init。

<a id="mobile"></a>

## 桌面、手机与其他客户端

后端启动后会同时托管桌面端和移动端 Web，都调用你配置的后端 API；Cookie 同步和平台登录仍由浏览器插件承担。桌面包从菜单栏 / 托盘打开 Web 界面，源码部署可运行 `openbiliclaw start`。

- **桌面端**：浏览器直接访问 `http://127.0.0.1:8420/web`（或 `http://127.0.0.1:8420/`，自动跳转）。大屏两栏布局，推荐流、30 天历史、画像、聊天、消息和设置全在一页。
- **移动端**：点击插件顶部的手机图标扫二维码，或手动输入 `http://<电脑局域网 IP>:8420/m/`。适合手机上刷推荐、回看 30 天历史、看画像和与阿B聊天。
- **Flutter 原生客户端**：从 [Latest Release](https://github.com/whiteguo233/OpenBiliClaw-mobile/releases/latest) 下载 Android APK（新机型选 `arm64-v8a`，老设备选 `armeabi-v7a`）直接安装，或下载 iOS 未签名 IPA 用个人 Apple 账号重签；装好后右上角设置里填后端 IP / 端口即可连接同一后端。局域网仍填电脑 IP；跨网络时 Android / iOS App 已内嵌 tsnet，可配合电脑端默认关闭的应用内 Tailnet 使用，两端加入同一 tailnet 后走 MagicDNS / Tailnet IP，无需电脑全局开 Tailscale，并建议开启应用密码。这里不包括该仓库的 Web / Linux / macOS / Windows Flutter 构建。

> 首次运行 `openbiliclaw init` 时会询问是否允许局域网访问（默认 Y）。如果选了 N 或想改回来，编辑 `config.toml` 的 `[api].host`（`0.0.0.0` = 通过可用的 IPv4 / IPv6 局域网访问，`127.0.0.1` = 仅本机）。二维码优先使用 IPv4；仅有 IPv6 时会自动生成带方括号的 IPv6 地址。

打开 `/m/` 后可以把手机页面保存成桌面快捷入口：iPhone / iPad 用 Safari 的「分享 → 添加到主屏幕」；Android Chrome / Chromium 浏览器用菜单里的「安装应用」或「添加到主屏幕」。局域网 HTTP 在部分 Android 浏览器上可能只生成快捷方式；如果想要更稳定的完整 PWA 安装提示，建议在可信环境里用 HTTPS 反代访问本机后端。

页面底部收敛为「推荐 / 内容库 / 画像 / 对话」四个一级 Tab。内容库内再按「稍后再看 / 收藏 / 历史记录」切换：前两项管理保存列表；历史记录按「主动点开过 / 出现过但没点开 / 最近移除」分页展示近 30 天内容，同一内容的多个移除原因会一起显示，收藏和稍后再看可以分别恢复。旧的稍后、收藏和历史直达链接会自动迁移到对应内容库子项。

## 手机跨网络：应用内 Tailnet

不在同一局域网时，`OpenBiliClaw-mobile` 的 **Android / iOS 原生 App** 已内嵌 `tsnet`，电脑端
也可让 OpenBiliClaw 自己加入同一 tailnet；Web / Linux / macOS / Windows Flutter 客户端不在
该能力范围。桌面安装包内置 helper；在电脑本机打开桌面 Web 或浏览器插件的
「设置 → 通用 → 应用内 Tailnet 远程访问」，开启后可选择留空走网页登录、填写
`tskey-auth-…` Auth Key，或填写带授权设备 tag 的 `tskey-client-…` OAuth Client Secret，
然后完整重启应用。凭据只在本机私有暂存到下一次启动，不进入 `config.toml`、API 回显或日志。
源码 / 一句话安装则先运行
`openbiliclaw tailnet build-helper`（需要 Go 1.26.6），再运行 `openbiliclaw tailnet enable`
并重启。电脑无需安装或全局开启系统 Tailscale；入口默认关闭、只在 tailnet 私网可见，
不启用 Funnel/Serve，建议同时在本机 Web 设置中开启应用密码（源码安装也可执行
`openbiliclaw set-password`）。详见[应用内 Tailnet](modules/tailnet.md)。

## 远程浏览器与 HTTPS

Chrome Web Store / AMO 发布包默认只声明本机后端权限。让插件连接局域网另一台机器或远程域名时，在设置里选择协议并填写地址，浏览器会请求该 `scheme://host/*` 的可选权限；WebExtension host permission 无法跨浏览器限定端口，但实际请求仍固定到配置端口。公网地址强制 HTTPS。后端需先用 `ext-key generate` 和 `ext-key enable` 开启默认关闭的设备认证。

有公网域名时，最短路径是叠加 [`docker-compose.https.yml`](../docker-compose.https.yml)，由 Caddy 自动申请和续期证书；PC、手机和插件共用 `https://<域名>`。命令与安全门禁见 [HTTPS 部署指南](https-deployment.md)。

## 其他部署方式

<a id="ai-deploy"></a>
<a id="ai-install"></a>

### AI 一句话部署

把下面整句粘给 Claude Code、Codex CLI、Cursor、Windsurf 或其他 AI 编程助手即可。括号里的限制是给 AI 助手看的，你不用理解。

```text
请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md 的说明帮我部署 OpenBiliClaw 后端(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch — 会丢关键指令)
```

AI 助手会克隆仓库、安装依赖、用局域网可访问的默认绑定启动后端（`0.0.0.0:8420`）、做健康检查，并问几个有默认值的问题。自动初始化前会真实验证全局 LLM 实例链和独立 embedding 服务；有一个不通就先停下让你修配置。小红书、抖音、YouTube、X、知乎、Reddit、Linux.do、Bangumi、V2EX、微博与 GitHub 信号只有你明确同意才会进入初始画像；微博个人事件需要已登录微博浏览器和扩展，Bangumi / GitHub 则需公开用户名或各自可选令牌解析出的身份。三者的公开发现仍可匿名进行。

源码安装默认不编译 Tailnet helper。只有用户明确需要 Android / iOS 原生 App 跨网络访问时，安装 Go 1.26.6
并在 checkout 中运行 `openbiliclaw tailnet build-helper`、`openbiliclaw tailnet enable`，然后
完整重启；只在局域网使用不需要这一步。Docker 首版镜像不内置该 helper。

<a id="script-install"></a>

### 安装脚本

macOS / Linux / WSL2（Bash）：

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

Windows 原生（PowerShell，不需要 Docker / WSL2）：

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

脚本依赖 `git` 和 Python 3.11+。它会自动克隆仓库，然后先在终端向导里收集首选 LLM 实例、embedding、B 站 Cookie，以及小红书 / 抖音 / YouTube 的 opt-in 决策，再安装依赖、启动后端和健康检查；确认齐全后会先验证全局 LLM 实例链和 embedding 服务都能真实响应，再自动运行 init，完成画像生成和首轮发现。X / 知乎 / Reddit / Linux.do / Bangumi / V2EX / 微博 / GitHub 可在启动后的 `/setup/` 或设置页显式开启；Linux.do、Bangumi、V2EX、微博与 GitHub 的公开 discovery 无需登录，微博个人初始化需要已登录微博浏览器和扩展，Bangumi / GitHub 个人初始化可填写公开用户名，GitHub PAT 仍为可选。不确定的选项直接回车或选默认。

<a id="docker"></a>

### Docker 部署

适合已经安装 Docker 的用户，自带 Ollama embedding sidecar。预构建镜像无需克隆源码：

```bash
mkdir -p ~/openbiliclaw && cd ~/openbiliclaw
curl -fsSLO https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docker-compose.prebuilt.yml
docker compose -f docker-compose.prebuilt.yml up -d
# 然后打开 http://127.0.0.1:8420/setup/ 完成初始化
```

也可以把下面这句粘给 AI 编程助手，走终端向导 + 自动 init：

```text
请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md 的说明帮我用 Docker Compose 部署 OpenBiliClaw 后端(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch)
```

源码构建、升级与排查详见 [Docker 部署指南](docker-deployment.md)。

<a id="manual"></a>
<a id="manual-install"></a>

### 手动安装与调试

> 人类维护者可以参考 [docs/agent-install.md](agent-install.md)(给智能体看的精简契约)和 [docs/agent-deployment.md](agent-deployment.md)(详细排查说明)。

#### 手动安装

```bash
# 克隆项目
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw

# 使用 uv (推荐)
uv sync

# 或使用 pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

#### 手动配置

```bash
# 复制配置模板
cp config.example.toml config.toml

# 编辑配置（设置 LLM API Key 等）
vim config.toml
```

#### 运行

下面的命令假定已经激活 `.venv`；使用 `uv sync` 的用户可在各命令前加 `uv run`。

```bash
# 如选本地 embedding，先准备独立向量服务（无需额外 API Key）
openbiliclaw setup-embedding

# 确认 LLM 与 embedding 可用后初始化（拉取历史 · 生成画像 · 首轮发现）
openbiliclaw init

# 手动触发内容发现
openbiliclaw discover

# 可选：抖音内容发现（需先启用 [sources.douyin]；search / hot / feed 从首页 DOM 操作触发）
openbiliclaw discover --source douyin

# 可选：Linux.do 书签 / 点赞 / 阅读记录只读 smoke（默认不写 memory）
openbiliclaw fetch-linuxdo

# 可选：Linux.do 正式发现（search / hot / feed / creator / related）
openbiliclaw discover-linuxdo --limit 30
# 等价：openbiliclaw discover --source linuxdo --limit 30

# 可选：独立调试抖音 search / hot / feed 召回
openbiliclaw discover-douyin --keyword 机械键盘 --source search,feed --no-cache --no-evaluate

# 可选：微博公开 discovery（需先启用 [sources.weibo]；公开读取不写画像）
openbiliclaw discover --source weibo
openbiliclaw discover-weibo 机械键盘
openbiliclaw discover-weibo-hot
openbiliclaw discover-weibo-creator 1234567890

# 查看推荐
openbiliclaw recommend

# 查看用户画像
openbiliclaw profile
```

开发者也可以从源码构建插件：

```bash
cd extension
npm install
npm run package
```

<a id="source-access"></a>

## 来源登录与限制

OpenBiliClaw 不保存你的平台密码，也不替你绕过登录。需登录的来源复用当前浏览器里的会话，匿名来源只读公开内容；两者都不会越过你能访问的边界。

| 源 | 登录方式 | 不登录的影响 |
|---|---|---|
| **B 站** | 在装了插件的浏览器打开 https://www.bilibili.com 正常登录 | 拉不到观看历史 / 收藏 / 关注，画像会明显变弱 |
| **小红书** | 在同一浏览器打开 https://www.xiaohongshu.com 正常登录 | 小红书 discovery 和详情抓取不可用 |
| **抖音** | 在同一浏览器打开 https://www.douyin.com 正常登录 | `init --yes-douyin`、`fetch-douyin` 和 `discover --source douyin` 的 search / hot / feed 可能返回 0 条 |
| **YouTube** | 在同一浏览器打开 https://www.youtube.com 正常登录 | `init --yes-youtube` 和 `fetch-youtube` 可能返回 0 条；仍可用 `import-youtube` 从 Takeout 导入 |
| **X（Twitter）** | 在同一浏览器打开 https://x.com 正常登录 | `init --yes-x`、`fetch-x` 和 X discovery 拉不到数据（服务端重放需要 `auth_token`+`ct0`，登录后扩展自动同步） |
| **知乎** | 在同一浏览器打开 https://www.zhihu.com 正常登录 | `init --yes-zhihu`、`fetch-zhihu`、`discover --source zhihu` 和 `discover-zhihu*` 拉不到数据 |
| **Reddit** | 在同一浏览器打开 https://www.reddit.com 正常登录；插件会同步 `reddit_session` 给日常 discovery 的 rdt-cli，`rdt login` 仅作为插件不可用时的 fallback | `fetch-reddit --mode bootstrap` 拉不到初始化信号；rdt credential 未同步时 rdt 路径会 fallback 到插件任务 |
| **Linux.do** | 在同一浏览器打开 https://linux.do 正常登录；公开 discovery 无需登录 | 未登录时 `fetch-linuxdo` 和 `init --yes-linuxdo` 拉不到书签 / 点赞 / 阅读记录，但 search / hot / feed / creator / related discovery 仍可用 |
| **Bangumi** | 无需登录；可选填公开用户名读取公开收藏，或填个人令牌读取私密收藏；插件在 bgm.tv / bangumi.tv 仅做账号身份自动识别（不读 Cookie、不采集浏览行为） | 未填用户名时不能把 Bangumi 作为唯一画像初始化来源，但匿名 search / ranked / 按日期 discovery 仍可用 |
| **V2EX** | 无需登录；可选填 PAT；guided init / 增量任务在扩展中读取本人主题、本人回复、收藏主题和收藏 Node 的公开渲染字段 | 未连接扩展时仍可匿名 search / node / tab / hot / latest discovery；收藏 scope 需要实际登录态 |
| **微博** | 公开发现无需登录；个人初始化需在同一浏览器登录 https://weibo.com 并连接插件 | 未登录时仍可发现公开内容，但不能读取个人初始化信号 |
| **GitHub** | 无需登录；可选填公开用户名读取公开 starred repositories，PAT 只用于提额和 `/user` 身份核验 | 未填身份时仍可匿名 search / ranked / latest discovery，但不能把 GitHub 作为唯一画像初始化来源；不读取浏览器 Cookie |

小红书、抖音、YouTube、知乎和 Linux.do 走 Chrome 插件任务链路，Reddit 日常 discovery 默认走随后端安装的 rdt-cli、初始化信号仍走插件，X 的 discovery 走服务端 cookie 重放；GitHub 则始终由后端官方 REST client 只读公开 repository，不进入插件任务。Linux.do 上游请求全部在真实站点 tab 内以同源 GET 执行，`_t` 只作登录布尔，Cookie 值和原始响应不会上传。Reddit/X、YouTube、小红书、抖音与知乎原生保存 executor 已 6/6 接入并通过 fixture 测试；2026-07-14 的真实账号回归中，六平台 favorite 与 watch-later/fallback 均得到 `synced/already_synced`。当前知乎收藏页已使用全局 `收藏 / 已收藏` 切换，目标文案为 `知乎收藏`；已收藏初始态和二次验证都严格只读，扩展不会重复点击保存。Linux.do 与 GitHub 不提供任何站内写回。`[sources.browser].cdp_url` 只保留给通用 Web / 自定义网页源的浏览器抓取场景。

## 本地 embedding / Ollama

如果你不想给 embedding 单独配置 API Key，或担心远程 embedding 配额，可以装一次 Ollama 后使用本地 `bge-m3`：

```bash
# macOS
# 安装并启动官方 Ollama.app（会创建 ollama 命令行入口）
open https://ollama.com/download/mac

# Linux
curl -fsSL https://ollama.com/install.sh | sh && ollama serve &
```

macOS / Windows 用户可以从 [ollama.com/download](https://ollama.com/download) 安装官方 App。启动 Ollama 后运行：

```bash
uv run openbiliclaw setup-embedding
```

向导会自动拉取 `bge-m3`（约 1.1GB，CPU 可跑）并写入配置。

## 遇到问题

先看 [FAQ](faq.md)，再按安装渠道查看 [部署排查](agent-deployment.md) 或 [Docker 指南](docker-deployment.md)。完整配置见 [配置参考](modules/config.md)，命令见 [CLI 参考](modules/cli.md)。
