<div align="center">

# 🦀 OpenBiliClaw

**通用个性化内容推荐 Agent——本地运行、跨平台理解你、只为你一个人构建**

*A general-purpose personalized content discovery Agent — runs on your machine, understands only you*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![LINUX DO](https://img.shields.io/badge/LINUX_DO-Community-black?style=flat-square&logo=linux)](https://linux.do/)
[![讨论帖](https://img.shields.io/badge/LINUX_DO-讨论帖-orange?style=flat-square&logo=discourse)](https://linux.do/t/topic/1978894)

[English](README_EN.md) | 中文

</div>

> 名字起源于 B 站（`Bili` = Bilibili，`Claw` = 爪子），项目最早只支持 B 站。从 v0.3.0 起已扩展为通用跨平台 Agent —— 已落地 B 站 / 小红书 / 通用 Web 三类源，持续接入更多内容平台。

---

## 📌 v0.3.0 重要更新（2026-04-28）

- **🌐 通用多源架构投产** —— 从"B 站推荐工具"演进为通用内容 Agent，小红书与通用 Web 适配器正式上线
- **🔌 本地 embedding 兜底** —— 可选 Ollama + bge-m3，无需额外 API Key 也能跑相似度计算（CPU 即可，跨 Mac/Win/Linux）
- **⚡ "换一批" 提速 5x** —— popup reshuffle 从 2.6s 降到 0.6s，连续刷无延迟
- **🎯 跨源主题去重** —— 同一主题在候选池里占比 ≤10%，告别"一刷都是 AI"

完整变更详见 [docs/changelog.md](docs/changelog.md)。

---

## 为什么需要 OpenBiliClaw？

推荐系统本质上是一个**中间商**——平台站在海量内容和海量用户之间做匹配分发。现代推荐系统远比「优化点击率」复杂：它同时权衡点击率、完播率、点赞/投币概率、停留时长、用户留存、创作者生态健康、广告收入等十几个目标，把它们加权压成一个分数来排序。听起来很科学，但问题在于：**这些权重是平台定的，优化目标归根结底是平台的**——用户满意度只是被当作留存和变现的手段，而非目的本身。你以为你在挑内容，其实是中间商在替你决定你能看到什么。结果就是：推荐越来越像你已经看过的东西，偶尔的惊喜全靠运气。

而且每个平台都是一座孤岛。你在 B 站看了三年机械键盘，小红书完全不知道；你在小红书种草的咖啡器具，B 站从来不会推给你。你的兴趣被割裂在不同平台的数据库里，没有人帮你把它们连起来。

**OpenBiliClaw 反过来。** 它是一个本地运行的 AI Agent——先深度理解你，再根据对你的理解**跨平台**主动搜寻你会喜欢的内容。项目从 B 站起步，现已扩展到小红书，未来将覆盖更多内容平台：

### 🧠 先懂你，再找内容

不是从视频出发匹配标签，而是从你出发。通过行为分析推断 MBTI、认知风格、深层心理需求，构建五层灵魂画像（事件→偏好→觉察→洞察→灵魂）。它理解的是你这个人，不是你的点击记录。

### 🔮 根据理解主动探索，而非被动匹配

这是和传统推荐最核心的差异：系统会基于对你的理解，**主动猜测你可能感兴趣但从未接触过的领域**。一个关注机械表的人可能会喜欢建筑美学，一个看量子物理科普的人可能对哲学感兴趣——它用心理学桥接逻辑主动出击，猜对了升级为正式兴趣，猜错了安静退出。协同过滤永远不会推给你「没人从这条路径走过」的内容，但 OpenBiliClaw 会。

### 🔒 100% 本地，100% 你的

所有数据留在你硬盘上的一个 SQLite 文件里。LLM 用你自己的 API Key。没有云端，没有账号，没有任何人能看到你的画像。这个 Agent 怎么长，完全你说了算——反馈推荐、对话调教、换 LLM、改数据库，随你。

> 💡 **和其他推荐工具的对比**
>
> | | 各平台官方推荐 | 关键词过滤插件 | OpenBiliClaw |
> |---|---|---|---|
> | 推荐逻辑 | 协同过滤 | 标签匹配 | 心理画像 + 五层记忆 |
> | 内容来源 | 单一平台 | 单一平台 | 跨平台（B 站 · 小红书 · 更多） |
> | 信息茧房 | 越推越窄 | 不解决 | 猜测兴趣主动破茧 |
> | 数据归属 | 平台所有 | 通常云端 | 100% 本地 |
> | 推荐解释 | "猜你喜欢" | 无 | 像朋友一样告诉你为什么 |
> | 可定制 | 不可以 | 低 | 换 LLM / 改画像 / 写 Skill |

## 📸 功能预览

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

## 🚀 快速开始

### 🧩 第一步：安装 Chrome 浏览器插件

插件是你和 OpenBiliClaw 交互的主要界面——在 B 站和小红书页面侧边栏展示推荐、采集行为、对话调教。

1. 打开 [OpenBiliClaw Releases](https://github.com/whiteguo233/OpenBiliClaw/releases)，找到最新的 `extension-v*` 发布
2. 下载其中的 `openbiliclaw-extension-v*.zip`
3. 打开 `chrome://extensions/`，开启右上角「开发者模式」
4. 将下载的 `.zip` 文件拖入页面安装

> 开发者也可以 `cd extension && npm install && npm run package` 从源码构建。

#### 重要：在装了插件的浏览器里**登录每一个想要使用的源**

OpenBiliClaw 不爬登录态——它复用**你**当前浏览器的登录会话来跨平台抓你能看到的内容。所以装好扩展后，请在**同一个浏览器**里登录你想用的每个源：

| 源 | 登录方式 | 不登录的后果 |
|---|---|---|
| **B 站** | https://www.bilibili.com 正常登录（Cookie 会被 v0.3.12+ 扩展自动同步给后端） | 拉不到你的观看历史/收藏/关注 → 画像不完整；推荐降级为公共热门 |
| **小红书** | https://www.xiaohongshu.com 正常登录 | 后端不会爬小红书，**所有发现/详情都靠扩展在隐藏 tab 里以你登录态执行**；不登录 = 完全没有小红书内容 |
| 通用 Web 源 | 该站点正常登录 | 同上 |

> 💡 **小红书强烈推荐用 CDP 模式 Chrome 复用登录态**（避免反爬）：用一个独立 profile 启 Chrome 打开 `--remote-debugging-port=9222`，里面手动登录小红书一次；后端 `[sources.browser] cdp_url = "http://localhost:9222"` 即可永久复用。详见 [配置参考](docs/modules/config.md#sourcesbrowser)。

### ⚡ 第二步：部署后端

**首选方式：复制粘贴给 AI 智能体一键部署**（Claude Code / Codex CLI / Cursor 等都支持）：

> 📌 **前置：你需要先有一个 AI 编程助手**。如果还没装，三选一：
> - [Claude Code](https://docs.anthropic.com/claude/docs/claude-code) — Anthropic 官方 CLI
> - [Codex CLI](https://github.com/openai/codex) — OpenAI 官方 CLI
> - [Cursor](https://cursor.com) / [Windsurf](https://codeium.com/windsurf) — 带 AI 的 IDE
>
> 没装过的话，跳到下面"自己跑一句话装机脚本"那段，效果一样。

```text
请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md 的说明帮我部署 OpenBiliClaw 后端(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch — 会丢关键指令)
```

AI 会按文档把仓库 clone 到本机、装依赖、起后端、做健康检查，**问你三个有默认值的问题**：选哪个 LLM、选哪个向量化(embedding)服务、怎么提供 B 站 Cookie。每一项都有"不确定就回 1"的默认推荐——v0.3.20+ 起 embedding 默认推荐本地 Ollama bge-m3（免费 + 离线 + 不消耗主 LLM 配额），同时清楚告诉你 Gemini 云端 embedding 质量更高的取舍。最后自动跑 `init`（拉历史 + 生成画像 + 首轮发现），全程透明。**这是最推荐的路径**，对绝大多数用户来说零摩擦。

**或者：让 AI 智能体用 Docker 部署**（适合有 Docker Desktop 的用户，v0.3.11+ 自带 Ollama embedding sidecar）：

```text
请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md 的说明帮我用 Docker Compose 部署 OpenBiliClaw 后端(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch)
```

**或者：自己跑一句话装机脚本**（不依赖 AI 智能体，本质上是 AI 装机的同一份脚本）：

macOS / Linux / WSL2（Bash）：

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

Windows 原生（PowerShell，不需要 Docker / WSL2）：

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

> 前缀的 `[Net.ServicePointManager]...Tls12` 是为了 PowerShell 5.1（Win10/Win11 默认）能和 GitHub 握手成功。GitHub 已不接受 TLS 1.0/1.1，PS 5.1 默认协议太老。装上 PowerShell 7 的用户可以省掉这段前缀。

脚本依赖只有 `git` 和 `python3`（3.11+，Windows 上推荐 `py launcher`）。会自动克隆仓库、装依赖、起后端、健康检查，然后提示你选 LLM 提供商（OpenAI / Gemini / DeepSeek / Claude / 本地 Ollama 等）并填 API Key + B 站 Cookie（v0.3.12+ 扩展自动同步）。凭据齐全后自动跑首次 init，直接达到可用状态。

<details>
<summary><b>不想跑脚本？也可以从 Releases 下载后端桌面包</b></summary>

适合不想动命令行的用户，但首版桌面包暂未做系统签名，**容易触发系统安全弹窗**，所以放在最后：

1. 打开 [OpenBiliClaw Releases](https://github.com/whiteguo233/OpenBiliClaw/releases)
2. 按系统下载后端包：
   - macOS：`OpenBiliClaw-macos-*.zip`
   - Windows：`OpenBiliClaw-windows-*.zip`
3. 解压后直接运行；macOS 会出 Gatekeeper 提示（右键打开），Windows 会出 SmartScreen 提示（点「更多信息」→ 仍要运行）
4. 让插件连接本地 `http://127.0.0.1:8420`

如果你嫌处理这些弹窗烦，回头用上面的「一句话装机」更省事。
</details>

> 💡 **Windows 用户**：v0.3.4 起 `install.ps1` 完全适配原生 Windows，无需安装 Docker 或 WSL2。已有 Docker Desktop 也可以用上面的 Docker 一键部署。

> 🧠 **可选：本地 embedding 兜底（无需 API Key）** —— 装一次 Ollama 就能跑：
> Mac `brew install ollama && ollama serve &`，Windows 从 [ollama.com/download](https://ollama.com/download) 下载，Linux `curl -fsSL https://ollama.com/install.sh \| sh && ollama serve &`。
> 然后 `uv run openbiliclaw setup-embedding`，向导自动拉取 `bge-m3`（~568MB，CPU 即可）并写入配置。适合 embedding 配额不够、断网，或不想再多一份 API Key 的用户。

<details>
<summary>手动安装 / 手动配置 / 浏览器插件</summary>

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

# 可选：启用本地 Ollama 作为 embedding 兜底（无需额外 API Key）
openbiliclaw setup-embedding

# 手动触发内容发现
openbiliclaw discover

# 查看推荐
openbiliclaw recommend

# 查看用户画像
openbiliclaw profile
```

#### Docker 部署

> 📦 也支持 Docker 一键部署，详见 [Docker 部署指南](docs/docker-deployment.md)

</details>

## 🤖 接入 OpenClaw / AI 编码助手

OpenBiliClaw 仓库内置了一个 [workspace skill](skills/openbiliclaw-adapter/SKILL.md)。把仓库挂到任何支持 skill 的 AI 编码助手（OpenClaw / Claude Code / Codex CLI / Cursor 等），助手就能直接调用你本机上的 OpenBiliClaw。

### 接入之后能干什么

- ✨ **主动推荐** — 系统在后台持续发现内容，遇到高分惊喜时通过 WebSocket 主动推送给 OpenClaw，OpenClaw 再转述给你——**你不需要开口问**
- 🔮 **主动追问兴趣** — 系统猜测你可能对某个方向感兴趣，生成一个假设和问题，通过 OpenClaw 主动来问你"这个方向你认不认？"——你回答后画像自动更新
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
- 🔮 **猜测兴趣系统** — 基于心理学桥接逻辑主动猜测你可能喜欢的未知领域，猜对升级、猜错退出，持续打破信息茧房
- 🌐 **跨平台内容源** — 从 B 站起步，已扩展到小红书，架构支持持续接入更多平台。你的兴趣不再被单一平台割裂
- 🔍 **多源发现策略** — B 站四策略（搜索 · 关联链 · 趋势 · 跨域探索）+ 小红书三层安全发现（被动收集 · 关键词搜索 · 创作者订阅），跨平台协同工作
- 🎯 **智能多样性** — PoolCurator 五维评分 + 跨源跨轮主题配额（任意 topic ≤10% 池子占比） + share-aware 池子修剪保护小源；告别"一刷都是 AI"
- ⚡ **"换一批"瞬间响应** — popup reshuffle ~0.6s（v0.3.0 从 2.6s 优化下来），连续刷不卡顿
- 💬 **有温度的推荐** — 不是"因为你看过类似视频"，而是像朋友一样解释为什么你会喜欢
- 🔄 **持续学习** — 苏格拉底式对话 + 行为分析 + 反馈即时生效，越用越懂你
- 🧩 **Chrome 浏览器插件** — 侧边栏展示推荐、跨站行为采集（B 站 + 小红书）、对话交互、认知更新卡片推送，装上就能用
- 🔬 **自动化评测优化** — 5 个模块各有 LLM-as-judge 的 SGD/RL 自优化循环，prompt 质量随轮次自动提升，不需要人工调参
- 🔒 **完全私有** — 所有数据本地 SQLite；LLM 用你自己的 Key；每个实例只为你一个人构建
- 🔌 **本地 embedding 兜底** — 可选 Ollama + bge-m3，不需要额外 embedding API Key 也能跑相似度计算（CPU 即可，跨 Mac/Win/Linux）
- 🔧 **完全可控** — 给每个模块单独换 LLM、直接编辑画像、写自定义 Skill 扩展发现策略

## 🏛️ 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                       Chrome Extension                       │
│ (行为采集 · 推荐展示 · 对话 · xhs 被动收集/任务/前台初始化)  │
└──────────────────────────┬──────────────────────────────────┘
                           │ REST API / WebSocket
┌──────────────────────────▼──────────────────────────────────┐
│                      Agent 编排层                            │
│                (Skill 系统 · 对话管理)                       │
├──────────┬──────────┬───────────┬────────────────────────────┤
│  Soul    │ Memory   │ Discovery │  Recommendation            │
│  Engine  │ System   │  Engine   │     Engine                  │
│ (画像)   │ (五层)   │ (多源适配) │   (跨源混排)               │
├──────────┴──────────┴───────────┴────────────────────────────┤
│       LLM 适配层  ·  B 站 API  ·  扩展代理发现  ·  SQLite      │
└─────────────────────────────────────────────────────────────┘
```

### 内容发现引擎

**多源适配架构**——通过 `SourceAdapter` 协议统一接入不同平台，每个平台有自己的发现方式：

| 来源 | 发现方式 | 说明 |
|------|----------|------|
| **B 站** | Search · Trending · Related Chain · Explore | 四大策略均衡协作，API 直连 |
| **小红书** | 被动收集 · 关键词搜索 · 创作者订阅 · 初始化画像导入 | 扩展驱动，安全无风控 |
| **通用 Web** | 浏览器 + LLM 抽取 | 适配任意网页 |

B 站走 API 直连（WBI 签名），小红书由扩展直接从页面 state / DOM 提取元数据和初始化画像信号（默认不深滚；初始化滚动任务会以前台 `/explore` 页点击“我”进入 profile，再有限滚动并分批回传；不后端爬取），通用 Web 走 Playwright CDP + LLM 内容提取。

发现结果经过多维度多样性选择：来源预留配额 → 主题去重 → 风格均衡 → **跨平台混排** → 上限封顶，确保最终推荐不局限于单一平台。

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
│   ├── soul/                  # 用户灵魂引擎 (深度画像 · MBTI · 兴趣猜测)
│   ├── memory/                # 多层网状记忆系统
│   ├── discovery/             # 内容发现引擎 (多源策略 · 配额均分 · 多样性选择)
│   ├── recommendation/        # 推荐与表达引擎 (跨平台混排)
│   ├── sources/               # 多源适配层 (SourceAdapter 协议)
│   │   ├── bilibili_adapter   # B 站 (API 直连)
│   │   ├── xiaohongshu_adapter # 小红书 (扩展代理)
│   │   ├── xhs_tasks          # 小红书插件任务队列 / bootstrap_profile
│   │   └── web_adapter        # 通用 Web (Playwright + LLM)
│   ├── bilibili/              # B 站接入层 (WBI 签名 · 速率控制)
│   ├── llm/                   # 多模型 LLM 适配
│   └── storage/               # 数据存储层
├── extension/                 # Chrome 浏览器插件 (B 站 + 小红书)
├── skills/                    # 内置 Skill 定义
├── docs/                      # 项目文档
└── tests/                     # 测试 (800+)
```

## 🛠️ 技术栈

| 模块 | 技术 |
|------|------|
| 后端 | Python 3.11+ |
| 浏览器插件 | TypeScript + Chrome Extension (Manifest V3) |
| LLM | 内置 Gemini / DeepSeek / OpenAI / Claude / OpenRouter / Ollama；支持任何兼容 OpenAI 协议的服务 |
| B 站交互 | 自研 API 客户端 (WBI 签名 · v_voucher 自动恢复 · 速率控制) |
| 小红书交互 | 扩展 DOM/state 元数据提取 + 插件任务调度；滚动型初始化会前台打开 `/explore` 并点击页面 profile 入口（零后端爬取） |
| 存储 | SQLite + Embedding 向量索引 |
| 容器化 | Docker Compose (后端) |
| Agent 框架 | 自研轻量框架 |

## 📖 文档

- [文档导航](docs/index.md) — 一站式文档入口
- [项目规格说明书](docs/spec.md) — 完整的项目设计与规划
- [架构设计](docs/architecture.md) — 系统架构详解
- [记忆系统设计](docs/memory-design.md) — 多层网状记忆架构
- [内容发现引擎](docs/modules/discovery.md) — 四策略发现 + 多样性选择
- [灵魂引擎](docs/modules/soul.md) — 深度画像 + MBTI + 兴趣猜测
- [CLI 参考](docs/modules/cli.md) · [配置参考](docs/modules/config.md)
- [开发指南](docs/contributing.md) — 如何参与贡献

## 📜 更新日志

| 版本 | 日期 | 主要变更 |
|---|---|---|
| **[v0.3.26](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.26)** | 2026-05-02 | LLM 计费模块:每次调用写一条到 `llm_usage` 表;`openbiliclaw cost` CLI 显示按天/按 provider 的花费分布。`config.example.toml` 默认值改成成本友好版(`reasoning_effort=""` 关思考链,`discovery_cron 8h`),新装用户日花费 ≈ ¥0.5 |
| [v0.3.25](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.25) | 2026-05-02 | discovery 评估 batch_size 10→30(摊薄 3500 token 系统提示 → -54% input),max_tokens 8192→16384;refresh `_requested_refresh_limit` 按 pool gap 缩放,gap=20 时每策略只请求 15 个候选(原 30) → -50-77% LLM 评估调用 |
| [v0.3.24](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.24) | 2026-05-02 | 跨源事件格式统一:B 站/小红书/扩展 click/feedback 全走 `event_format.build_event()`,emit 带中文自然语言 `context` 的标准化 dict;profile_builder 的 `_summarize_history` 输出新增 `contexts` 列表;preference / awareness / soul 提示词加 rule 引导 LLM 优先用 context;修 DB context 列双重 JSON 编码(导致 LLM prompt triple-escaped) |
| [v0.3.23](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.23) | 2026-05-02 | xhs `bootstrap_profile` 滚动任务改为前台 tab(后台 tab 在小红书上无法触发瀑布流懒加载);滚动容器探测优先 feed/waterfall/masonry,排除零高度 wrapper;profile state 解析补齐 `displayTitle` / `cover.urlDefault` |
| [v0.3.22](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.22) | 2026-05-01 | 修 `openbiliclaw init` 让小红书数据真的进画像:CLI 等待时间从 8s 改成"开头入队 + 30s 等待 collect",`max_scroll_rounds` 默认 0→3,5 种完成状态(ok/empty/timeout/failed/skipped)分别打中文反馈 |
| [v0.3.21](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.21) | 2026-05-01 | 把 v0.3.20 的 UX 改动对齐到 docker / Windows PowerShell / CLI 直跑向导:`docker-deployment.md` 主菜单 DeepSeek 默认 + 自建网关挪到高级;`install.ps1` cookie-only 绿字 + REUSE_FROM 警告;`cli.py` `_LLM_MENU` 重排 + embedding 向导重写为默认推荐 Ollama bge-m3 |
| **[v0.3.20](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.20)** | 2026-05-01 | 装机流程 UX 修复 + Embedding fallback：Claude/DeepSeek/OpenRouter 主模型 + 跟随 LLM 的 embedding 静默失败修复（用新增的 `LLMProvider.supports_embedding` 标志做 fallback，自动回退到 ollama → gemini → openai）· `--provider openai` 不带 `--llm-base-url` 时自动清空残留网关 URL · 主菜单去掉自建网关 · Embedding 改成"有默认值的取舍提问"（推荐本地 Ollama bge-m3，同时说明 Gemini 云端质量更高的取舍）· 状态摘要改成"等扩展同步 Cookie"绿字，不再吓人 · README 加 AI Agent 前置说明 |
| [v0.3.19](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.19) | 2026-05-01 | `openbiliclaw init` 会 best-effort 混入小红书收藏 / 点赞 / 页面内浏览记录信号。插件在用户已登录的小红书会话中执行 `bootstrap_profile`，滚动任务会先以前台 `/explore` 页点击“我”进入当前用户 profile，再读收藏 / 赞过 state；显式滚动任务会用 `partial` 批次持续回传，后端把 notes 转成普通 `favorite / like / view` 事件，不直接爬小红书 |
| **[v0.3.18](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.18)** | 2026-04-30 | 把 `franchise_key` 升成 content_cache 一等字段：LLM 评估时直接打 IP 标签（不再用硬编码 alias 表），下游 curator dislike 传播 + `/api/recommendations` 同 IP 去重全都基于这一列。撤掉 v0.3.17 的标题 heuristic |
| [v0.3.17](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.17) | 2026-04-30 | 修推荐流过度泛化 IP（一屏 5 条原神 / 提瓦特）：新增 heuristic franchise 提取器；`/api/recommendations` 最终对同 IP 去重（同一 franchise 最多出现 2 次）；点踩 1 条原神视频会软降权所有同 IP 候选，不再只屏蔽单条 |
| [v0.3.16](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.16) | 2026-04-30 | README 后端安装方式重排：一句话装机 / Docker / 自跑脚本 优先，未签名桌面包后置（折叠 details）· 新增「多源登录前置」段，明确小红书必须在装扩展的浏览器里登录（CDP 模式更稳） |
| [v0.3.15](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.15) | 2026-04-30 | 一连串 Windows 装机踩坑修复：CLI 启动强制 stdout=UTF-8 防 GBK 控制台 emoji 崩 · install.ps1 的 `python -c f"..."` 改成 `print(a, b)` 绕开 PS 5.1 引号 bug · agent-install.md 警告 Windows 上 `bash` 误踩 WSL · **修 Ollama embedding-only registry 误进 chat fallback 导致 「All providers failed」** |
| [v0.3.14](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.14) | 2026-04-30 | 修 Windows GBK 默认编码导致 `/api/delight/pending-batch`、`/api/activity-feed` 等接口在简体中文 Windows 上返回 500：`MemoryLayer.load()/save()` 显式 `encoding="utf-8"`，`bilibili.auth` 同步加固。带 monkeypatch `builtins.open` 的回归测试 |
| [v0.3.13](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.13) | 2026-04-30 | 各种安装路径都把「装扩展自动同步」放到 Cookie 步骤的首选：install.sh / install.ps1 / agent-install.md / CLI 向导 / docker-deployment.md / openclaw-quickstart.md 全部更新；F12 路径降级为兜底 |
| [v0.3.12](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.12) | 2026-04-30 | 浏览器扩展自动同步 B 站 Cookie 到后端，再也不用 F12：扩展用 `chrome.cookies` API 读登录态 → 后端 `POST /api/bilibili/cookie` 校验 + 持久化 + 热重载 + WebSocket 广播。装好扩展几秒内自动登录态，Cookie 过期也会自动续 |
| [v0.3.11](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.11) | 2026-04-30 | Docker 模式自带 Ollama embedding sidecar（自动拉 bge-m3 + named volume 持久化）· `docker_runtime.py` 启动时按 env 自动 seed `[llm.embedding] provider=ollama` · CLI 向导（`openbiliclaw init` 直跑）也支持自动装 Ollama |
| [v0.3.10](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.10) | 2026-04-30 | 选 Ollama 时一句话装机自己装 Ollama + 拉模型：检测 → brew/winget/install.sh 自动装 → 后台 `ollama serve` → `ollama pull` 拉所需模型，全程 stream 进度。新增 `ollama_ready` 等 JSON 事件 |
| [v0.3.9](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.9) | 2026-04-30 | 一句话装机适配 PowerShell 5.1（Win10/Win11 默认 PS 版本）：命令前缀加 TLS 1.2 设置 + 修 `??` PS 7-only 语法 + 脚本内自带 TLS 1.2 兜底 |
| [v0.3.8](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.8) | 2026-04-30 | `openbiliclaw init` 开头打印「预计 2–5 分钟」+ 4 阶段耗时分布，避免用户以为卡住 |
| [v0.3.7](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.7) | 2026-04-30 | 一句话装机配齐凭据后**自动跑 `openbiliclaw init`**（拉历史 / 生成画像 / 首轮发现），不再让用户多走一步 · agent-install.md Hard Rule 翻转：默认跑 init · agent_bootstrap.py auto-init 修 Windows/Docker 路径 |
| [v0.3.6](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.6) | 2026-04-30 | 装机向导从普通用户视角彻底重写：Ollama 排第一作为默认 · OpenAI 官方与协议兼容自建网关拆成两个菜单项 · Embedding 单独提问附带解释 · B 站 Cookie 教用户怎么 F12 拿 |
| [v0.3.5](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.5) | 2026-04-29 | 装机向导改 4 阶段（base_url / 三件套 / embedding 4 选 1 / per-module 覆盖）· 不再因 `openai = 协议家族` 歧义猜错 · `agent_bootstrap.py` 新增 7 个 flag |
| [v0.3.4](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.4) | 2026-04-29 | 原生 Windows 一句话装机（PowerShell `install.ps1`，无需 Docker/WSL2）· `agent_bootstrap.py` Windows 适配（taskkill / netstat-ano） |
| **[v0.3.0](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.0)** | 2026-04-28 | 通用多源架构（xhs/web 适配器投产）· 本地 Ollama embedding 兜底 · reshuffle 5x 提速 · 跨源主题配额 |
| [v0.2.1](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/v0.2.1) | 2026-04-17 | OpenClaw 集成（苏格拉底对话 + 兴趣探针）· bilibili API 容错强化 |
| [v0.2.0](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/v0.2.0) | 2026-04-16 | macOS .app 包修复 · 多目标推荐评论框架 · 推荐池硬上限 · 五维 PoolCurator |
| [v0.1.0](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/v0.1.0) | 2026-04-13 | 首版发布——soul / discovery / recommendation 全链路打通 |

完整里程碑变更：[docs/changelog.md](docs/changelog.md) · 所有发布：[GitHub Releases](https://github.com/whiteguo233/OpenBiliClaw/releases)

## 🗺️ 后续规划

OpenBiliClaw 的目标是做你的**全网个性化内容入口**——从 B 站起步、v0.3.0 已落地小红书与通用 Web 适配器，下一步：

- **更多内容源** — 知乎、V2EX、抖音、微博、各类 BBS / 论坛……每个平台都是一个 `SourceAdapter`，架构已经验证可扩展
- **跨平台兴趣融合** — 你在 B 站看的机械键盘 + 小红书种草的咖啡器具 = 一个完整的你。画像融合让推荐不再割裂
- **更智能的发现** — 跨平台关联推荐（"你在小红书关注了咖啡器具，B 站有个手冲咖啡纪录片你可能喜欢"）
- **社区生态** — 用户自定义 SourceAdapter、共享发现策略、贡献平台适配器

## 🤝 贡献

欢迎贡献！请查看 [开发指南](docs/contributing.md) 了解如何参与。

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=whiteguo233/OpenBiliClaw&type=Date)](https://www.star-history.com/#whiteguo233/OpenBiliClaw&Date)

## 📄 License

[MIT](LICENSE)
