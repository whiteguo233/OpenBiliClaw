<div align="center">

# 🦀 OpenBiliClaw

**B 站推荐算法的开源替代品——跑在你自己电脑上，只懂你一个人**

*An open-source alternative to Bilibili's recommendation algorithm — runs on your machine, understands only you*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

[English](README_EN.md) | 中文

</div>

---

## 为什么需要 OpenBiliClaw？

传统推荐系统——无论是协同过滤（「看了 A 的人也看了 B」）还是深度排序模型——本质上都在优化点击率和完播率。它们知道你*会点什么*，却从不问你*为什么点*。结果就是：推荐越来越像你已经看过的东西，偶尔的惊喜全靠运气。

**OpenBiliClaw 反过来。** 它是一个本地运行的 AI Agent——先深度理解你，再根据对你的理解**主动**去全站搜寻你会喜欢的内容：

### 🧠 先懂你，再找内容

不是从视频出发匹配标签，而是从你出发。通过行为分析推断 MBTI、认知风格、深层心理需求，构建五层灵魂画像（事件→偏好→觉察→洞察→灵魂）。它理解的是你这个人，不是你的点击记录。

### 🔮 根据理解主动探索，而非被动匹配

这是和传统推荐最核心的差异：系统会基于对你的理解，**主动猜测你可能感兴趣但从未接触过的领域**。一个关注机械表的人可能会喜欢建筑美学，一个看量子物理科普的人可能对哲学感兴趣——它用心理学桥接逻辑主动出击，猜对了升级为正式兴趣，猜错了安静退出。协同过滤永远不会推给你「没人从这条路径走过」的内容，但 OpenBiliClaw 会。

### 🔒 100% 本地，100% 你的

所有数据留在你硬盘上的一个 SQLite 文件里。LLM 用你自己的 API Key。没有云端，没有账号，没有任何人能看到你的画像。这个 Agent 怎么长，完全你说了算——反馈推荐、对话调教、换 LLM、改数据库，随你。

> 💡 **和其他推荐工具的对比**
>
> | | B 站官方 | 关键词过滤插件 | OpenBiliClaw |
> |---|---|---|---|
> | 推荐逻辑 | 协同过滤 | 标签匹配 | 心理画像 + 五层记忆 |
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

插件是你和 OpenBiliClaw 交互的主要界面——在 B 站页面侧边栏展示推荐、采集行为、对话调教。

1. 下载最新的 [`openbiliclaw-extension.zip`](https://github.com/whiteguo233/OpenBiliClaw/releases/latest)
2. 打开 `chrome://extensions/`，开启右上角「开发者模式」
3. 将下载的 `.zip` 文件拖入页面安装

> 开发者也可以 `cd extension && npm install && npm run package` 从源码构建。

### ⚡ 第二步：部署后端

**⭐ 复制粘贴给 AI 智能体一键部署（推荐，Claude Code / Codex CLI / Cursor 等都支持）：**

```text
请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md 的说明帮我部署 OpenBiliClaw 后端(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch — 会丢关键指令)
```

**⭐ 让 AI 智能体用 Docker 部署（推荐，适合有 Docker Desktop 的用户）：**

```text
请按照 https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md 的说明帮我用 Docker Compose 部署 OpenBiliClaw 后端(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch)
```

**终端一条命令：**

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

支持 macOS / Linux / WSL2。依赖只有 `git` 和 `python3`（3.11+）。脚本会自动克隆仓库、安装依赖、启动后端、做健康检查，然后提示你选择 LLM 提供商（OpenAI / Gemini / DeepSeek / Claude 等）并填写对应的 API Key 和 B 站 Cookie。凭据就绪后自动完成首次初始化（拉取历史、生成画像、填充推荐池），直接达到可用状态。

> 💡 **Windows 用户？** 如果你已经装了 Docker Desktop，推荐直接用上面的 Docker 方式部署，开箱即用。否则请先安装 [WSL2](https://learn.microsoft.com/zh-cn/windows/wsl/install) 再用终端命令安装。

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

## ✨ 核心特性

- 🧠 **五层灵魂画像** — 事件→偏好→觉察→洞察→灵魂，推断 MBTI、认知风格和深层需求，像心理咨询师一样理解你
- 🔮 **猜测兴趣系统** — 基于心理学桥接逻辑主动猜测你可能喜欢的未知领域，猜对升级、猜错退出，持续打破信息茧房
- 🔍 **四大发现策略** — 搜索、关联链、趋势、跨域探索协同工作，均衡配额，像资深 B 站用户一样帮你找好内容
- 🎯 **智能多样性** — PoolCurator 五维评分（相关性 · 新鲜度 · 主题疲劳 · 来源单调度 · 惊喜度），确保每次推荐都有惊喜而不是千篇一律
- 💬 **有温度的推荐** — 不是"因为你看过类似视频"，而是像朋友一样解释为什么你会喜欢
- 🔄 **持续学习** — 苏格拉底式对话 + 行为分析 + 反馈即时生效，越用越懂你
- 🧩 **Chrome 浏览器插件** — 侧边栏展示推荐、实时行为采集、对话交互、认知更新卡片推送，装上就能用
- 🔬 **自动化评测优化** — 5 个模块各有 LLM-as-judge 的 SGD/RL 自优化循环，prompt 质量随轮次自动提升，不需要人工调参
- 🔒 **完全私有** — 所有数据本地 SQLite；LLM 用你自己的 Key；每个实例只为你一个人构建
- 🔧 **完全可控** — 给每个模块单独换 LLM、直接编辑画像、写自定义 Skill 扩展发现策略

## 🏛️ 架构概览

```
┌─────────────────────────────────────────────────────┐
│                   Chrome Extension                   │
│              (行为采集 · 推荐展示 · 对话)              │
└────────────────────────┬────────────────────────────┘
                         │ REST API
┌────────────────────────▼────────────────────────────┐
│                    Agent 编排层                       │
│              (Skill 系统 · 对话管理)                  │
├─────────┬──────────┬───────────┬────────────────────┤
│  Soul   │ Memory   │ Discovery │  Recommendation    │
│  Engine │ System   │  Engine   │     Engine          │
│ (画像)  │ (五层)   │ (四策略)   │   (表达)            │
├─────────┴──────────┴───────────┴────────────────────┤
│         LLM 适配层  ·  B 站 API  ·  SQLite           │
└─────────────────────────────────────────────────────┘
```

### 内容发现引擎

四大策略均衡协作，每个策略独立 API 配额：

| 策略 | 描述 | 配额 |
|------|------|------|
| **Search** | 基于兴趣 + 猜测兴趣生成搜索词 | 均分 |
| **Trending** | 多分区排行榜热门内容 | 均分 |
| **Related Chain** | 从种子视频沿推荐链扩展 | 均分 |
| **Explore** | LLM 驱动的跨域探索 | 均分 |

发现结果经过多维度多样性选择：来源预留配额 → 主题去重 → 风格均衡 → 上限封顶，确保最终推荐覆盖广泛。

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
│   ├── discovery/             # 内容发现引擎 (四策略 · 配额均分 · 多样性选择)
│   ├── recommendation/        # 推荐与表达引擎
│   ├── bilibili/              # B 站接入层 (WBI 签名 · 速率控制)
│   ├── llm/                   # 多模型 LLM 适配
│   └── storage/               # 数据存储层
├── extension/                 # Chrome 浏览器插件
├── skills/                    # 内置 Skill 定义
├── docs/                      # 项目文档
└── tests/                     # 测试 (650+)
```

## 🛠️ 技术栈

| 模块 | 技术 |
|------|------|
| 后端 | Python 3.11+ |
| 浏览器插件 | TypeScript + Chrome Extension (Manifest V3) |
| LLM | 内置 Gemini / DeepSeek / OpenAI / Claude / OpenRouter / Ollama；支持任何兼容 OpenAI 协议的服务（自定义 base_url 即可接入） |
| B 站交互 | 自研 API 客户端 (WBI 签名 · v_voucher 自动恢复 · 速率控制) |
| 存储 | SQLite + Embedding 向量索引 |
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

## 🤝 贡献

欢迎贡献！请查看 [开发指南](docs/contributing.md) 了解如何参与。

## 📄 License

[MIT](LICENSE)
