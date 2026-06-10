<div align="center">

# 🦀 OpenBiliClaw

**通用个性化内容推荐 Agent——本地运行、跨平台理解你、只为你一个人构建**

*A general-purpose personalized content discovery Agent — runs on your machine, understands only you*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![LINUX DO](https://img.shields.io/badge/LINUX_DO-Community-black?style=flat-square&logo=linux)](https://linux.do/)
[![讨论帖](https://img.shields.io/badge/LINUX_DO-讨论帖-orange?style=flat-square&logo=discourse)](https://linux.do/t/topic/1978894)
[![Chrome 应用商店](https://img.shields.io/chrome-web-store/v/cdfjfkdjjhdaccbldipkjhpibnfbiamg?style=flat-square&label=Chrome%20应用商店&logo=googlechrome&logoColor=white&color=4285F4)](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg)

[项目主页](https://whiteguo233.github.io/OpenBiliClaw/) | [English](README_EN.md) | 中文

</div>

## 10 秒看懂 OpenBiliClaw

一个纯本地、私有、开源的自进化跨平台内容发现 Agent：从你的跨平台使用、反馈和对话中持续深化心理画像，带着对你的理解主动去 B 站、小红书、抖音、YouTube、X 等来源找内容。

| 跨平台 | 本地优先 | 可调教 |
|---|---|---|
| B 站 / 小红书 / 抖音 / YouTube / X / Web | 数据默认留在本机 SQLite | 喜欢、不感兴趣、聊天反馈都会改变后续推荐 |

<p align="center">
  <a href="https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg"><b>安装浏览器插件</b></a>
  ·
  <a href="#快速开始"><b>让 AI 助手部署后端</b></a>
</p>

<p align="center">
  <sub>喜欢这个方向？<a href="https://github.com/whiteguo233/OpenBiliClaw">欢迎 Star 支持项目继续适配更多平台</a>。</sub>
</p>

<p align="center">
  <img src="docs/images/hero-demo-zh.gif" width="820" alt="OpenBiliClaw 跨平台本地推荐 Agent 演示：信号进入本地后端、生成画像、解释推荐理由、根据反馈继续学习" />
</p>

## 快速开始

普通用户先走这四步；Firefox、Docker 和手动部署等备用路径保留在后面的 [安装与部署详情](#安装与部署详情)。

1. **安装浏览器插件**：推荐从 [Releases](https://github.com/whiteguo233/OpenBiliClaw/releases) 下载最新 `extension-v*` 的 zip 手动安装（版本最新）；也可从 [Chrome 应用商店](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg)一键安装（自动更新，但受审核排期影响，版本可能滞后于 Releases）。
2. **部署后端（两种方式，按需选一，都推荐）**：
   - 🖥️ **下载桌面安装包（最省事）**：到 [Releases](https://github.com/whiteguo233/OpenBiliClaw/releases) 下载 macOS `.dmg` / Windows `.exe`，装好双击即用 —— 自带本地 embedding、常驻菜单栏/托盘。当前为**未签名的实验性预发布**，首次打开需绕过系统拦截，详见 [安装与部署详情](#安装与部署详情)。
   - 🤖 **让 AI 助手部署（想改源码 / 深度定制选它）**：把下面整句粘给 Claude Code、Codex CLI、Cursor、Windsurf 或其他 AI 编程助手。

```text
请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md 的说明帮我部署 OpenBiliClaw 后端(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch — 会丢关键指令)
```

3. **在同一个浏览器登录内容平台**：至少登录 [B 站](https://www.bilibili.com)，需要多源信号时再登录 [小红书](https://www.xiaohongshu.com) / [抖音](https://www.douyin.com) / [YouTube](https://www.youtube.com) / [X](https://x.com)。
4. **打开桌面端或移动端 Web**：后端启动后访问 `http://127.0.0.1:8420/web`；手机可扫插件二维码打开 `http://<电脑局域网 IP>:8420/m/`。

## 为什么需要 OpenBiliClaw？

> 名字起源于 B 站（`Bili` = Bilibili，`Claw` = 爪子），项目最早只支持 B 站。从 v0.3.0 起已扩展为通用跨平台 Agent —— 已落地 B 站 / 小红书 / 抖音 / YouTube / X 初始化信号、抖音 search / hot / feed 与 X 服务端内容发现和通用 Web 多类源，持续接入更多内容平台。

推荐系统本质上是一个**中间商**——平台站在海量内容和海量用户之间做匹配分发。现代推荐系统远比「优化点击率」复杂：它同时权衡点击率、完播率、点赞/投币概率、停留时长、用户留存、创作者生态健康、广告收入等十几个目标，把它们加权压成一个分数来排序。听起来很科学，但问题在于：**这些权重是平台定的，优化目标归根结底是平台的**——用户满意度只是被当作留存和变现的手段，而非目的本身。你以为你在挑内容，其实是中间商在替你决定你能看到什么。结果就是：推荐越来越像你已经看过的东西，偶尔的惊喜全靠运气。

而且每个平台都是一座孤岛。你在 B 站看了三年机械键盘，小红书完全不知道；你在小红书种草的咖啡器具，B 站从来不会推给你。你的兴趣被割裂在不同平台的数据库里，没有人帮你把它们连起来。

**OpenBiliClaw 反过来。** 它是一个本地运行的 AI Agent——先深度理解你，再根据对你的理解**跨平台**主动搜寻你会喜欢的内容。项目从 B 站起步，现已扩展到小红书、抖音、YouTube 和 X（Twitter），后续还会覆盖更多内容平台：

### 🧠 先懂你，再找内容

不是从视频出发匹配标签，而是从你出发。通过行为分析推断 MBTI、认知风格、深层心理需求，构建五层灵魂画像（事件→偏好→觉察→洞察→灵魂）。它理解的是你这个人，不是你的点击记录。

### 🔮 根据理解主动探索，而非被动匹配

这是和传统推荐最核心的差异：系统会基于对你的理解，**主动猜测你可能感兴趣但从未接触过的领域**。一个关注机械表的人可能会喜欢建筑美学，一个看量子物理科普的人可能对哲学感兴趣——它用心理学桥接逻辑主动出击，猜对了升级为正式兴趣，猜错了安静退出。协同过滤永远不会推给你「没人从这条路径走过」的内容，但 OpenBiliClaw 会。

### 🔒 100% 本地，100% 你的

所有数据留在你硬盘上的一个 SQLite 文件里。LLM 默认用你自己的 API Key，也可实验性复用本机 Codex CLI 的 ChatGPT OAuth 凭据。没有云端，没有账号，没有任何人能看到你的画像。这个 Agent 怎么长，完全你说了算——反馈推荐、对话调教、换 LLM、改数据库，随你。

> 💡 **和其他推荐工具的对比**
>
> | | 各平台官方推荐 | 关键词过滤插件 | OpenBiliClaw |
> |---|---|---|---|
> | 推荐逻辑 | 协同过滤 | 标签匹配 | 心理画像 + 五层记忆 |
> | 内容来源 | 单一平台 | 单一平台 | 跨平台（B 站 · 小红书 · 抖音 · YouTube · X · 更多） |
> | 信息茧房 | 越推越窄 | 不解决 | 猜测兴趣主动破茧 |
> | 数据归属 | 平台所有 | 通常云端 | 100% 本地 |
> | 推荐解释 | "猜你喜欢" | 无 | 像朋友一样告诉你为什么 |
> | 可定制 | 不可以 | 低 | 换 LLM / 改画像 / 写 Skill |

## 📸 功能预览

核心入口现在有三个：浏览器插件负责平台内交互和登录会话，桌面端 Web（`/web`）提供大屏推荐首页，移动端 Web（`/m`）适合手机使用。桌面端和移动端都只调用本地 API，Cookie 同步和平台任务仍由插件承担。

<table>
  <tr>
    <td align="center" width="25%">
      <img src="docs/images/screenshot-recommend.png" width="200" /><br/>
      <b>智能推荐</b><br/>
      <sub>像朋友一样解释为什么你会喜欢</sub>
    </td>
    <td align="center" width="25%">
      <img src="docs/images/screenshot-profile-portrait.png" width="200" /><br/>
      <b>灵魂画像</b><br/>
      <sub>自然语言描述的深度人格分析</sub>
    </td>
    <td align="center" width="25%">
      <img src="docs/images/screenshot-profile-traits.png" width="200" /><br/>
      <b>结构化特质</b><br/>
      <sub>MBTI · 核心特质 · 深层需求</sub>
    </td>
    <td align="center" width="25%">
      <img src="docs/images/screenshot-chat.png" width="200" /><br/>
      <b>对话调教</b><br/>
      <sub>聊天告诉它你想看什么</sub>
    </td>
  </tr>
</table>

### 🖥️ 桌面端 Web 预览

启动后端后访问 `http://127.0.0.1:8420/web`（或直接 `http://127.0.0.1:8420/`，会自动跳转），即可在浏览器大屏上使用推荐首页。

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/images/desktop-home.png" width="480" /><br/>
      <b>桌面推荐首页</b><br/>
      <sub>惊喜推荐 Hero · 为你推荐网格 · 朋友式推荐理由</sub>
    </td>
    <td align="center" width="50%">
      <img src="docs/images/desktop-cards.png" width="480" /><br/>
      <b>推荐卡片网格</b><br/>
      <sub>封面 + 推荐理由 · 喜欢 / 不感兴趣 / 稍后 / 收藏 / 聊一聊</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="docs/images/desktop-profile.png" width="480" /><br/>
      <b>画像 + 实时看板</b><br/>
      <sub>侧栏 Runtime 看板 + 后台动态 · 人格素描 · 核心特质 · MBTI 推断</sub>
    </td>
  </tr>
</table>

### 📱 移动端 Web 预览

<table>
  <tr>
    <td align="center" width="33%">
      <img src="docs/images/mobile-recommend.png" width="210" /><br/>
      <b>手机推荐页</b><br/>
      <sub>惊喜推荐 + 池子状态 · 朋友式推荐原因</sub><br/>
      <sub>看看 / 喜欢 / 稍后 / 收藏 / 不感兴趣 / 聊一聊</sub>
    </td>
    <td align="center" width="33%">
      <img src="docs/images/mobile-profile.png" width="210" /><br/>
      <b>手机画像页</b><br/>
      <sub>人格素描 · 核心特质 · 深层需求 · MBTI</sub>
    </td>
    <td align="center" width="33%">
      <img src="docs/images/mobile-chat.png" width="210" /><br/>
      <b>手机对话页</b><br/>
      <sub>与插件共享主聊天历史</sub>
    </td>
  </tr>
</table>

<details>
<summary>更多截图</summary>

<table>
  <tr>
    <td align="center" width="33%">
      <img src="docs/images/screenshot-recommend-feedback.png" width="200" /><br/>
      <b>推荐反馈</b><br/>
      <sub>点赞 / 多来点 / 少来点 / 没兴趣</sub>
    </td>
    <td align="center" width="33%">
      <img src="docs/images/screenshot-profile-values.png" width="200" /><br/>
      <b>价值偏好与兴趣</b><br/>
      <sub>内在驱动力 · 猜测兴趣方向</sub>
    </td>
    <td align="center" width="33%">
      <img src="docs/images/screenshot-profile-style.png" width="200" /><br/>
      <b>认知风格</b><br/>
      <sub>信息处理偏好 · 内容口味</sub>
    </td>
  </tr>
</table>

</details>

## 最近更新

最新版本：**v0.3.115: 自动更新解卡（2026-06-10）**。完整变更详见 [docs/changelog.md](docs/changelog.md)。

- **自动更新真的会更新了** —— 修复发布 lock 文件过期导致所有 git 安装的自动更新被「脏工作树」守卫永久拦截、且毫无提示的问题。
- **设置页能看到更新状态** —— 自动更新开关下新增状态行：当前 / 最新版本、阻塞原因、上次检查时间，不再「开了没反应」。
- **桌面安装包如实提示** —— 桌面包结构上不支持 git 自更新，现在禁用开关并提示下载新版安装包，不再静默无效。
- **存量安装一次解卡** —— 旧安装在安装目录执行一次 `git checkout -- uv.lock && git pull`（或重跑一键安装脚本）即可恢复自动更新。

## 用户交流群

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/images/user-community-qrcode.png" width="200" alt="QQ 用户交流群二维码" /><br/>
      <b>QQ 用户群</b>
    </td>
    <td align="center" width="50%">
      <img src="docs/images/wechat-user-community-qrcode.jpg" width="200" alt="微信用户群二维码" /><br/>
      <b>微信用户群</b><br/>
      <sub>二维码 7 天内有效，失效后会更新</sub>
    </td>
  </tr>
</table>

## 安装与部署详情

普通用户的正常流程是：先安装浏览器插件，再把一句话发给 AI 助手安装后端，在同一个浏览器登录内容平台；如果要在手机上使用，再打开移动端 Web。脚本、Docker 和手动部署只作为备用路径，放在下面折叠区。

### 1. 安装浏览器插件

插件是主要入口：它会在 B 站、小红书、抖音、YouTube 和 X 页面显示侧边栏、采集你的反馈，并把浏览器登录态安全地交给本地后端使用。

插件基于 Manifest V3，支持所有兼容 Chrome 插件的浏览器，包括 **Chrome、Edge、Brave、Arc、Vivaldi、Opera** 等。

**推荐方式 · 从 Releases 下载最新版手动安装**（拿到最新功能与修复 —— Chrome 应用商店受审核排期影响，版本通常会滞后几天到一两周）：

1. 打开 [OpenBiliClaw Releases](https://github.com/whiteguo233/OpenBiliClaw/releases)，找到最新的 `extension-v*`
2. Chrome / Edge / Brave 下载 `openbiliclaw-extension-v*.zip`；Firefox 下载 `openbiliclaw-extension-v*-firefox.zip`
3. 打开扩展管理页面（Chrome：`chrome://extensions/` · Edge：`edge://extensions/` · Brave：`brave://extensions/`），开启右上角「开发者模式」
4. 将下载的 `.zip` 文件拖入页面安装

**省事方式 · Chrome 应用商店一键安装**（安装后由浏览器自动更新，适合不想手动升级的人；缺点是版本可能滞后于 Releases）：

> 👉 **[在 Chrome 应用商店安装 OpenBiliClaw](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg)** —— 打开后点「添加至 Chrome」即可。

插件更新取决于安装渠道：Chrome Web Store / Edge Add-ons / AMO 安装的版本由浏览器自动更新；从 GitHub Release 下载 zip、开发者模式加载或 Firefox 临时加载的用户，需要下载新版 zip 并按同样方式重新加载。后端设置里的“自动更新”开关只更新本地后端源码，不会更新浏览器插件。

<details>
<summary>Firefox 用户：下载 Firefox 包临时加载（Firefox 140+）</summary>

Firefox 用 `sidebar_action` 而不是 Chrome 的 `sidePanel`，所以 release 会提供独立的 `openbiliclaw-extension-v*-firefox.zip`。下载后先解压，再通过 `about:debugging` 临时加载；也可以从源码本地构建同一个 Firefox 包：

```bash
unzip openbiliclaw-extension-v*-firefox.zip -d openbiliclaw-firefox

# 或从源码构建
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw/extension
npm install
npm run build:firefox          # 产出 dist-firefox/
# 或: npm run package:firefox   # 额外打成 openbiliclaw-extension-v*-firefox.zip
```

加载方式：

1. 打开 `about:debugging#/runtime/this-firefox`
2. 点「Load Temporary Add-on…」
3. 选解压目录里的 `manifest.json`（或源码构建后的 `extension/dist-firefox/manifest.json`）

注意：Firefox 临时加载在浏览器重启后会失效；正式签名 / AMO 上架仍在规划中。

</details>

### 2. 部署后端（二选一）

普通用户直接用**桌面安装包**最省事；想改源码、换 LLM、深度定制就用 **AI 一句话部署**。

#### 方式 A：下载桌面安装包（实验性，最省事）

到 [Releases](https://github.com/whiteguo233/OpenBiliClaw/releases) 下载对应系统的安装包：

- **macOS**：`.dmg`（区分 Apple 芯片 `arm64` / Intel `x64`），打开后把 OpenBiliClaw 拖进「应用程序」。
- **Windows**：`.exe` 安装程序，双击安装。

安装包自带本地 Ollama + `bge-m3` embedding，开箱即用；启动后常驻 **macOS 菜单栏 / Windows 系统托盘**，右键可「打开 Web 界面 / 查看运行日志 / 退出」。数据与 AI / 脚本安装复用同一个目录：`~/OpenBiliClaw`（macOS / Linux）/ `%USERPROFILE%\OpenBiliClaw`（Windows），升级或卸载不会动它；旧安装包曾写入的 `~/Library/Application Support/OpenBiliClaw` / `%LOCALAPPDATA%\OpenBiliClaw` 会在新版本首次启动时非覆盖拷贝回来。

> ⚠️ **首次打开要绕过系统拦截（应用尚未签名 / 公证）**：
> - **macOS**：先拖进「应用程序」，再右键图标 →「打开」→ 在弹窗里再点「打开」；或到「系统设置 → 隐私与安全性」点「仍要打开」。若仍提示“已损坏”，确认包来自本项目 Releases 后执行：
>
>   ```bash
>   APP="/Applications/OpenBiliClaw.app"
>   xattr -dr com.apple.quarantine "$APP"
>   codesign --force --deep --sign - "$APP"
>   open "$APP"
>   ```
> - **Windows**：SmartScreen 弹窗点「更多信息 → 仍要运行」。
>
> 这是**实验性预发布**：未签名、随后端版本滚动更新，适合只想最快试用、不碰命令行的人。要二次开发 / 改源码请用下面的方式 B。

#### 方式 B：AI 一句话部署（可定制 / 可改源码）

把下面整句粘给 Claude Code、Codex CLI、Cursor、Windsurf 或其他 AI 编程助手即可。括号里的限制是给 AI 助手看的，你不用理解。

```text
请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md 的说明帮我部署 OpenBiliClaw 后端(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch — 会丢关键指令)
```

AI 助手会克隆仓库、安装依赖、用局域网可访问的默认绑定启动后端（`0.0.0.0:8420`）、做健康检查，并问几个有默认值的问题。自动初始化前会真实验证 LLM provider 和 embedding 服务；有一个不通就先停下让你修配置，不会硬跑出空画像。看不懂就选默认；小红书、抖音、YouTube 和 X 数据只有你明确同意才会进入初始画像。

Chrome Web Store / AMO 发布包默认只声明本机后端权限，插件侧建议保持 `127.0.0.1` / `localhost`。如果要让手机访问移动端 Web，可继续用 `openbiliclaw start --host 0.0.0.0 --port 8420` 启动后端，插件二维码会优先展示电脑的局域网 IP。让插件直接连接局域网另一台机器或远程域名，需要带对应 host 权限的开发者构建，或等待后续可选授权开关。

### 3. 在同一个浏览器登录内容平台

至少登录 [B 站](https://www.bilibili.com)，OpenBiliClaw 会用它生成第一版画像和推荐。想接入小红书、抖音、YouTube 或 X 时，再在装了插件的同一个浏览器里登录 [小红书](https://www.xiaohongshu.com) / [抖音](https://www.douyin.com) / [YouTube](https://www.youtube.com) / [X](https://x.com)。

### 4. 打开桌面端或移动端 Web

后端启动后会同时托管桌面端和移动端 Web，都只调用本地 API，不做 Cookie 同步或平台登录。

```bash
openbiliclaw start
```

- **桌面端**：浏览器直接访问 `http://127.0.0.1:8420/web`（或 `http://127.0.0.1:8420/`，自动跳转）。大屏两栏布局，推荐流、画像、聊天、消息和设置全在一页。
- **移动端**：点击插件顶部的手机图标扫二维码，或手动输入 `http://<电脑局域网 IP>:8420/m/`。适合手机上刷推荐、看画像、和阿B聊天。

> 首次运行 `openbiliclaw init` 时会询问是否允许局域网访问（默认 Y）。如果选了 N 或想改回来，编辑 `config.toml` 的 `[api].host`（`0.0.0.0` = 局域网可达，`127.0.0.1` = 仅本机）。

页面包含「推荐 / 稍后 / 收藏 / 画像 / 对话」五个底部 Tab：推荐页支持「换一批 / 加载更多 / 喜欢 / 不感兴趣 / 稍后再看 / 收藏 / 写一句 / 聊一聊」，稍后和收藏页管理「稍后再看 / 收藏」列表，画像页展示人格素描、核心特质、兴趣和认知更新，对话页与插件共享主聊天历史。

<details>
<summary>不用 AI 助手：直接跑一句话安装脚本</summary>

macOS / Linux / WSL2（Bash）：

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

Windows 原生（PowerShell，不需要 Docker / WSL2）：

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

脚本依赖 `git` 和 Python 3.11+。它会自动克隆仓库，然后先在终端向导里收集 LLM provider、embedding、B 站 Cookie、小红书 opt-in、抖音 opt-in、YouTube opt-in、X opt-in 等决策，再安装依赖、启动后端和健康检查；确认齐全后会先验证 LLM provider 和 embedding 服务都能真实响应，再自动运行 init，完成画像生成和首轮发现。不确定的选项直接回车或选默认。

</details>

<details>
<summary>高级：Docker 部署</summary>

适合已经安装 Docker Desktop 的用户。v0.3.11+ 自带 Ollama embedding sidecar。

```text
请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md 的说明帮我用 Docker Compose 部署 OpenBiliClaw 后端(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch)
```

详见 [Docker 部署指南](docs/docker-deployment.md)。Docker 主路径同样走 `agent_bootstrap.py --mode docker`，会在确认 LLM、embedding、B 站 Cookie 和各来源 opt-in 后先验证 AI 服务，再自动运行 init；`docker exec ... openbiliclaw init` 只作为高级手动 fallback。

</details>

<details>
<summary>高级：多源登录与插件链路</summary>

OpenBiliClaw 不保存你的平台密码，也不替你绕过登录。它复用当前浏览器里的登录会话，只抓你自己能看到的内容。

| 源 | 登录方式 | 不登录的影响 |
|---|---|---|
| **B 站** | 在装了插件的浏览器打开 https://www.bilibili.com 正常登录 | 拉不到观看历史 / 收藏 / 关注，画像会明显变弱 |
| **小红书** | 在同一浏览器打开 https://www.xiaohongshu.com 正常登录 | 小红书 discovery 和详情抓取不可用 |
| **抖音** | 在同一浏览器打开 https://www.douyin.com 正常登录 | `init --yes-douyin`、`fetch-douyin` 和 `discover --source douyin` 的 search / hot / feed 可能返回 0 条 |
| **YouTube** | 在同一浏览器打开 https://www.youtube.com 正常登录 | `init --yes-youtube` 和 `fetch-youtube` 可能返回 0 条；仍可用 `import-youtube` 从 Takeout 导入 |
| **X（Twitter）** | 在同一浏览器打开 https://x.com 正常登录 | `init --yes-x`、`fetch-x` 和 X discovery 拉不到数据（服务端重放需要 `auth_token`+`ct0`，登录后扩展自动同步） |

小红书、抖音和 YouTube 走 Chrome 插件任务链路，X 走服务端 cookie 重放（扩展只负责同步 x.com cookie + 捕获互动），都不需要你额外启动 CDP 调试 Chrome。`[sources.browser].cdp_url` 只保留给通用 Web / 自定义网页源的浏览器抓取场景。

</details>

<details>
<summary>高级：本地 embedding / Ollama</summary>

如果你不想给 embedding 单独配置 API Key，或担心远程 embedding 配额，可以装一次 Ollama 后使用本地 `bge-m3`：

```bash
# macOS
brew install ollama && ollama serve &

# Linux
curl -fsSL https://ollama.com/install.sh | sh && ollama serve &
```

Windows 用户可以从 [ollama.com/download](https://ollama.com/download) 安装。安装后运行：

```bash
uv run openbiliclaw setup-embedding
```

向导会自动拉取 `bge-m3`（约 568MB，CPU 可跑）并写入配置。

</details>

<details>
<summary>高级：手动安装与 discovery 调试</summary>

> 人类维护者可以参考 [docs/agent-install.md](docs/agent-install.md)(给智能体看的精简契约)和 [docs/agent-deployment.md](docs/agent-deployment.md)(详细排查说明)。

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

```bash
# 一键初始化（拉取历史 · 生成画像 · 首轮发现）
openbiliclaw init

# 可选：启用本地 Ollama 作为独立 embedding provider（无需额外 API Key）
openbiliclaw setup-embedding

# 手动触发内容发现
openbiliclaw discover

# 可选：抖音内容发现（需先启用 [sources.douyin]；search / hot / feed 用后台插件签名）
openbiliclaw discover --source douyin

# 可选：独立调试抖音 search / hot / feed 召回
openbiliclaw discover-douyin --keyword 机械键盘 --source search,feed --no-cache --no-evaluate

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

</details>

## 🤖 接入 OpenClaw / AI 编码助手

OpenBiliClaw 仓库内置了一个 [workspace skill](skills/openbiliclaw-adapter/SKILL.md)。把仓库挂到任何支持 skill 的 AI 编码助手（OpenClaw / Claude Code / Codex CLI / Cursor 等），助手就能直接调用你本机上的 OpenBiliClaw。

### 接入之后能干什么

- ✨ **主动推荐** — 系统在后台持续发现内容，遇到高分惊喜时通过 WebSocket 主动推送给 OpenClaw，OpenClaw 再转述给你——**你不需要开口问**
- 🔮 **主动追问兴趣** — 系统猜测你可能对某个方向感兴趣，生成一个假设和问题，通过 OpenClaw 主动来问你"这个方向你认不认？"——你回答后画像自动更新
- 🧭 **主动确认避雷** — 系统也会确认你可能想避开的内容形态，OpenClaw 可用 `next-avoidance-probe` / `respond-avoidance-probe` 完成确认；只有确认后才写入过滤偏好
- 💬 **苏格拉底式对话** — 不止是确认兴趣，OpenClaw 可以跟你深聊：追问动机、提出假设、确认理解，越聊越懂你
- 📖 **读当前灵魂画像** — MBTI、核心特质、深层需求、兴趣领域
- 🎯 **按需拉个性化推荐** — 带解释、带置信度、带主题标签
- 💬 **把反馈写回学习闭环** — `like` / `dislike` / `comment` 即时更新画像与池子评分
- 🔄 **同步 B 站账号行为** — 拉历史、收藏、关注等长期信号，注入记忆系统

### 一句话让 OpenClaw 完成接入

把下面这段粘给 OpenClaw（或 Claude Code / Codex CLI / Cursor），它会自动读指南并完成接入：

```text
请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/openclaw-quickstart.md 的说明帮我把当前仓库接入 OpenClaw(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch — 会丢关键指令)
```

### 用户使用示例

接入后不只是"你问它答"——**系统会主动找你**。以下是两个最核心的场景：

#### 场景一：系统主动推一条惊喜内容

OpenClaw 在后台开着 `listen`，某次 refresh 后系统发现了一条高分内容：

> **OpenClaw**（收到 WebSocket 推送 `delight.candidate`，主动找你）：
>
> > 刚发现一条你可能会意外喜欢的：
> > **《为什么说回县城你也躺不平》** — 县域经济的明斯基时刻。你最近一直在盯地缘政治和宏观债务，这条用硬核推演把土地财政和债务周期的齿轮咬合讲清楚了。`[BV1KMwuzdEcB]`
> >
> > 要不要看看？
>
> **你**："这条非常到位。"
>
> **OpenClaw**（内部执行 `submit-feedback --recommendation-id 4268 --feedback-type like`）

你没有开口要推荐——是系统主动捅过来的。

#### 场景二：系统主动追问你的兴趣

OpenClaw 收到 `interest.probe` 事件（或主动拉取 `next-probe`），发现有一个猜测兴趣假设待确认：

> **OpenClaw**（主动找你）：
>
> > 我从你最近的轨迹里嗅到你可能对【建筑美学】（比如：参数化设计、混凝土美学）感兴趣——你最近看了很多关于结构和空间的内容。这个方向你自己认不认？
>
> **你**："嗯对，最近在看很多参数化设计的东西，安藤忠雄那种清水混凝土的美感很戳我。"
>
> **OpenClaw**（内部执行 `chat --message "嗯对，最近在看很多参数化设计的东西……"`，返回苏格拉底式追问）：
>
> > 你说的这个方向我有个猜测——你是不是其实更在意底层结构而不只是结论？安藤忠雄那种用最少元素构建最大张力的方式，跟你平时看大模型逻辑时追求的"把齿轮咬合讲清楚"好像是同一种审美驱动。你觉得呢？

对话内容自动回写进灵魂画像——下一轮推荐就会把建筑美学纳入正式兴趣，搜索策略也会开始往这个方向发力。

#### 场景三：你也可以主动要推荐

当然，传统的"你问→它答"也完全支持：

> **你**："给我推三条今天值得看的 B 站内容。"
>
> **OpenClaw**（内部执行 `recommend --limit 3`，整理后回复）

整个闭环都是本地的——OpenClaw 只是调 CLI 桥接，画像和数据仍留在你自己的 SQLite 文件里，一条都不会上云。

> 📖 完整命令参考与常见问题，见 [OpenClaw 接入指南](docs/openclaw-quickstart.md)。

## ✨ 核心特性

- 🧠 **五层灵魂画像** — 事件→偏好→觉察→洞察→灵魂，推断 MBTI、认知风格和深层需求，像心理咨询师一样理解你
- 🔮 **挑战式兴趣探针** — 基于心理学桥接逻辑主动猜测你可能喜欢的未知领域，按 near/lateral/bridge/wildcard 控制距离；普通池最多 5 条，挑战池单独最多 3 条，弱正向先进入短期 buffer，避免一次确认后短期刷屏
- 🧭 **不喜欢领域探针** — 主动确认你可能想避开的内容形态、低质表达或风格边界；确认后写入 `disliked_topics` 并清理候选池，未确认前不影响推荐
- 🌐 **跨平台内容源** — 从 B 站起步，已扩展到小红书、抖音、YouTube 初始化信号 / 抖音 search / hot / feed discovery / X（Twitter）服务端 cookie 重放发现 和通用 Web，架构支持持续接入更多平台。你的兴趣不再被单一平台割裂
- 🔍 **多源发现策略** — B 站四策略（搜索 · 关联链 · 趋势 · 跨域探索）+ 小红书三层安全发现 + 抖音插件签名 search / hot / feed + X 搜索 / For-You / 关注作者，跨平台协同工作
- 🎯 **智能多样性** — PoolCurator 五维评分 + 跨源跨轮主题配额（任意 topic ≤10% 池子占比） + share-aware 池子修剪保护小源；告别"一刷都是 AI"
- ⚡ **"换一批"瞬间响应** — popup reshuffle ~0.6s（v0.3.0 从 2.6s 优化下来），连续刷不卡顿
- 💬 **有温度的推荐** — 不是"因为你看过类似视频"，而是像朋友一样解释为什么你会喜欢
- 🔄 **持续学习** — 苏格拉底式对话 + 行为分析 + 反馈即时生效，越用越懂你
- 🧩 **浏览器插件（Chrome / Edge / Brave / Arc 等）** — 侧边栏展示推荐、跨站行为采集（B 站 + 小红书 + 抖音 + YouTube + X）、对话交互、认知更新卡片推送，装上就能用
- 🚀 **图形化引导初始化** — 不想进命令行也行：安装包首启 `/setup/`、桌面 Web `/web` 未初始化空态和插件「推荐」tab 都会列出前置清单（B 站登录 / LLM / embedding），点「开始初始化」就能一步步看着画像与首轮内容池建起来（命令行 `openbiliclaw init` 仍是等价入口）
- 🔬 **自动化评测优化** — 5 个模块各有 LLM-as-judge 的 SGD/RL 自优化循环，prompt 质量随轮次自动提升，不需要人工调参
- 🔒 **完全私有** — 所有数据本地 SQLite；LLM 用你自己的 Key；每个实例只为你一个人构建
- 🔌 **本地 embedding provider** — 可选 Ollama + bge-m3，不需要额外 embedding API Key 也能跑相似度计算（CPU 即可，跨 Mac/Win/Linux）
- 🔧 **完全可控** — 给每个模块单独换 LLM、直接编辑画像、写自定义 Skill 扩展发现策略

## 🏛️ 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                       Chrome Extension                       │
│        (行为采集 · 停留满意度 · 推荐展示 · 跨源点击信号 · 对话 · 探针) │
│        (Cookie 同步 · xhs/dy/yt 任务调度 · 初始化画像导入 · 自启动设置)│
└──────────────────────────┬──────────────────────────────────┘
                           │ REST API / WebSocket（presence + Cookie 同步 + 可换库存 + source-aware click + 探针）
                           │ + 移动/桌面 Web（/m · /web）· 可选 [api.auth] 密码门禁（本机免登录 / LAN 需密码）
┌──────────────────────────▼──────────────────────────────────┐
│                      Agent 编排层                            │
│              (Skill 系统 · 对话管理 · Runtime Gate · 账号同步)  │
├──────────┬──────────┬───────────┬────────────────────────────┤
│  Soul    │ Memory   │ Discovery │  Recommendation            │
│  Engine  │ System   │  Engine   │     Engine                  │
│(画像+探针)│ (五层+buffer)│(待评估池+负样本)│ (跨源混排+放大保护)       │
├──────────┴──────────┴───────────┴────────────────────────────┤
│       LLM 适配层(API Key/Codex OAuth) · B 站 API · 扩展代理发现   │
│       Runtime: Account Sync + producers + probe arbiter + autostart/Ollama │
│       Runtime status: pool_available/raw/pending/eval_count        │
│       SQLite: events · discovery_candidates · content_cache        │
│               recommendations · chat_turns · avoidance_state        │
│       Profile overrides: 画像编辑 -> profile_overrides.json 覆盖层      │
│               (get_profile/sync 读时叠加 · 抗画像重建 · 三端编辑)       │
└─────────────────────────────────────────────────────────────┘
```

### 内容发现引擎

**多源适配架构**——通过 `SourceAdapter` 协议统一接入不同平台，每个平台有自己的发现方式：

| 来源 | 发现方式 | 说明 |
|------|----------|------|
| **B 站** | Search · Trending · Related Chain · Explore | 四大策略均衡协作，API 直连 |
| **小红书** | 被动收集 · 关键词搜索 · 创作者订阅 · 初始化画像导入 | 扩展驱动，安全无风控 |
| **抖音** | 初始化画像导入 · 后台搜索 · 热点关联 · 首页推荐流 | 扩展驱动，强信号入画像；日常发现走登录浏览器后台标签页签名补池，不抢用户焦点 |
| **YouTube** | 初始化画像导入 · Google Takeout 离线导入 · 后端直连补池 | 扩展读取观看历史 / 订阅 / 点赞入画像；Takeout 可补旧历史；日常发现由后端搜索 / 热门 / 频道独立补池 |
| **X（Twitter）** | 初始化点赞 / 收藏导入 · 关键词搜索 · For-You · 关注作者 | 服务端 cookie 重放（`twitter-cli`，只读，可选 extra `openbiliclaw[x]`）；扩展捕获你在 x.com 的互动并同步 cookie；推文为纯文本卡片 |
| **通用 Web** | 浏览器 + LLM 抽取 | 适配任意网页 |

**安全取数**——B 站和通用 Web 由后端直连（B 站走 WBI 签名 API），小红书 / 抖音 / YouTube 则由浏览器扩展在你*已登录的页面*里读取数据：初始化画像默认不深滚、分批回传，后端既不直接爬取也不代登录，YouTube 还可用 Google Takeout 离线导入旧历史。X 则由后端用扩展同步来的 x.com cookie（`auth_token`+`ct0`）做服务端只读重放，扩展只负责同步 cookie 和捕获你自己的互动。日常补池时，抖音用登录浏览器的后台标签页签名取数、不抢你的前台焦点，YouTube 由后端按平台缺口直接补。

**统一评估**——各来源先把 raw candidates 写入 `discovery_candidates`，后端再按来源混合取样，统一用 Soul 画像和近期负反馈样本做 batch 评估。真正判断“你会不会喜欢”发生在这个共享 evaluator，而不是分散在每个平台自己的 producer 里。

**多样性选择**——通过评估的候选再经过 平台配额预留 → 主题去重 → 风格均衡 → **跨平台混排** → 数量封顶，避免“一刷都是 AI”。各平台保存配比默认 B 站 / 小红书 / 抖音 / YouTube / X = 5 / 1 / 1 / 1 / 1，可在 `[scheduler.pool_source_shares]` 调整；开箱只启用 B 站，其余平台需显式打开。

**候选池计数**——界面上的“可换”只统计 `pool_available_count`：已生成推荐文案、带分类与可跳转链接、且不在最近已看窗口内的内容；还在整理中的素材计入 `pool_pending_count`，其中 `pool_pending_eval_count` / `pool_evaluated_pending_count` 拆出待评估与已评估待入池阶段，插件 / 移动端 / 桌面端都不会把它显示成“可换”。

### 灵魂引擎

从用户行为中推断：
- **人格画像** — 自然语言描述的用户画像
- **MBTI** — 四维度 + 置信度
- **认知风格** — 信息处理偏好
- **深层需求** — 心理层面的内容驱动力
- **猜测兴趣** — 系统推测的潜在兴趣方向（分子料理、建筑美学、制表工艺...）

## 🏗️ 项目结构

```
OpenBiliClaw/
├── src/openbiliclaw/          # Python 后端核心
│   ├── agent/                 # Agent 编排和 Skill 系统
│   ├── soul/                  # 用户灵魂引擎 (深度画像 · MBTI · 兴趣/避雷探针)
│   ├── memory/                # 多层网状记忆系统
│   ├── discovery/             # 内容发现引擎 (多源策略 · 待评估池 · 配额均分 · 多样性选择)
│   ├── recommendation/        # 推荐与表达引擎 (跨平台混排)
│   ├── sources/               # 多源适配层 (SourceAdapter 协议)
│   │   ├── bilibili_adapter   # B 站 (API 直连)
│   │   ├── xiaohongshu_adapter # 小红书 (扩展代理)
│   │   ├── xhs_tasks          # 小红书插件任务队列 / bootstrap_profile
│   │   ├── dy_tasks           # 抖音插件任务队列 / bootstrap_profile + search + hot + feed
│   │   ├── yt_tasks           # YouTube 插件任务队列 / bootstrap_profile
│   │   └── web_adapter        # 通用 Web (Playwright + LLM)
│   ├── youtube/               # YouTube Takeout 离线导入解析
│   ├── api/                   # 本地 FastAPI (配置回滚 / 降级模式 / popup API)
│   ├── runtime/               # 后台刷新、presence gate、autostart/Ollama、降级 RuntimeContext
│   ├── bilibili/              # B 站接入层 (WBI 签名 · 速率控制)
│   ├── llm/                   # 多模型 LLM 适配 + 结构化 JSON 容错
│   └── storage/               # 数据存储层
├── extension/                 # Chrome 浏览器插件 (B 站 + 小红书 + 抖音 + YouTube + X + 自启动/配置修复)
├── skills/                    # 内置 Skill 定义
├── docs/                      # 项目文档
└── tests/                     # 测试 (1900+)
```

## 🛠️ 技术栈

| 模块 | 技术 |
|------|------|
| 后端 | Python 3.11+ |
| 浏览器插件 | TypeScript + Chrome Extension (Manifest V3) |
| LLM | 内置 Gemini / DeepSeek / OpenAI / Claude / OpenRouter / Ollama；支持任何兼容 OpenAI 协议的服务；OpenAI provider 可实验性复用 Codex CLI OAuth |
| B 站交互 | 自研 API 客户端 (WBI 签名 · v_voucher 自动恢复 · 速率控制) |
| 小红书交互 | 扩展 DOM/state 元数据提取 + 插件任务调度；滚动型初始化会前台打开 `/explore` 并点击页面 profile 入口（零后端爬取） |
| 抖音交互 | 扩展 DOM + MAIN-world fetch/API harvester + 插件任务调度；初始化导入发布 / 收藏 / 点赞 / 关注信号，search / hot / feed discovery 用后台 tab 复用登录浏览器签名桥（零后端爬取） |
| YouTube 交互 | 扩展 DOM 任务调度读取观看历史 / 订阅 / 点赞；Google Takeout 可离线导入旧数据 |
| X 交互 | 服务端 cookie 重放（`twitter-cli` lazy import，可选 `openbiliclaw[x]`，只读）；扩展捕获你在 x.com 的互动并同步 cookie；推文为纯文本卡片 |
| 存储 | SQLite + Embedding 向量索引 |
| 容器化 | Docker Compose (后端) |
| Agent 框架 | 自研轻量框架 |

## 📖 文档

- [文档导航](docs/index.md) — 一站式文档入口
- [项目规格说明书](docs/spec.md) — 完整的项目设计与规划
- [架构设计](docs/architecture.md) — 系统架构详解
- [记忆系统设计](docs/memory-design.md) — 多层网状记忆架构
- [内容发现引擎](docs/modules/discovery.md) — 多源发现 + 平台配比 + 多样性选择
- [灵魂引擎](docs/modules/soul.md) — 深度画像 + MBTI + 兴趣猜测
- [CLI 参考](docs/modules/cli.md) · [配置参考](docs/modules/config.md)
- [开发指南](docs/contributing.md) — 如何参与贡献

## 📜 更新日志

最新版本：**v0.3.115: 自动更新解卡（2026-06-10）**。最近更新见上方摘要；完整历史见 [docs/changelog.md](docs/changelog.md)。插件包和桌面安装包见 [GitHub Releases](https://github.com/whiteguo233/OpenBiliClaw/releases)，后端源码更新看 `backend-v*` tag。

## 🗺️ 后续规划

OpenBiliClaw 的目标是做你的**全网个性化内容入口**——从 B 站起步，已落地小红书、抖音、YouTube 初始化信号，抖音 search / hot / feed discovery、X（Twitter）服务端发现与通用 Web 适配器，下一步：

- **更多内容源** — 知乎、V2EX、微博、各类 BBS / 论坛……每个平台都是一个 `SourceAdapter`，架构已经验证可扩展
- **跨平台兴趣融合** — 你在 B 站看的机械键盘 + 小红书种草的咖啡器具 + 抖音点赞收藏的短视频偏好 + YouTube 长视频观看和订阅 + X 点赞收藏的资讯 = 一个完整的你。画像融合让推荐不再割裂
- **更智能的发现** — 跨平台关联推荐（"你在小红书关注了咖啡器具，B 站有个手冲咖啡纪录片你可能喜欢"，或用抖音 feed 口味补足短视频兴趣）
- **社区生态** — 用户自定义 SourceAdapter、共享发现策略、贡献平台适配器

## 🤝 贡献

欢迎贡献！请查看 [开发指南](docs/contributing.md) 了解如何参与。

## 🙏 致谢

- 感谢 [@addtion99](https://github.com/addtion99) 在 [#8](https://github.com/whiteguo233/OpenBiliClaw/pull/8) 提出浏览器插件后端地址 / 端口可配置需求，并给出 popup 侧实现思路。
- 感谢 [@jiaobenhaimo](https://github.com/jiaobenhaimo) 在 [#53](https://github.com/whiteguo233/OpenBiliClaw/pull/53) 贡献 Safari 扩展、稍后再看、YouTube 搬运检测、营销号过滤等功能设计与实现，其中 OR-join 去重修复和稍后再看功能已合入主线。

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=whiteguo233/OpenBiliClaw&type=Date)](https://www.star-history.com/#whiteguo233/OpenBiliClaw&Date)

## 隐私速览

默认数据流向：浏览器插件 → 你配置的本地 OpenBiliClaw 后端 → 本机 SQLite。插件不会把数据发送到 OpenBiliClaw 开发者运营的服务器。若你配置云端 LLM / embedding，相关内容会按你的配置发送给对应服务商。详见 [隐私政策](docs/privacy.md)。

## 📄 License

[MIT](LICENSE)
