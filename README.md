<div align="center">

# 🦀 OpenBiliClaw

**你的 B 站专属 AI 朋友，比你更懂你想看什么**

*Your personal AI companion for Bilibili — discovers content you'll love but can't find on your own*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

[English](README_EN.md) | 中文

</div>

---

OpenBiliClaw 是一个开源的 Bilibili 个性化内容推荐 AI Agent。它不是一个冷冰冰的推荐算法，而是像一个真正了解你的朋友——理解你是什么样的人、为什么喜欢某些内容，然后主动在 B 站帮你发现你会喜欢但自己找不到的东西。

> 🔐 **严格单用户 · 完全本地 · 专属于你一个人**
>
> OpenBiliClaw 不是 SaaS，不是多用户平台。每个人安装一份独立的实例，每份实例只围绕你一个人建立理解——你的画像、记忆和推荐权重永远不会和其他用户混在一起。
>
> - **你的数据留在你硬盘上** — 灵魂画像、五层记忆、对话历史、B 站行为日志全部存在本地 SQLite 文件（`data/openbiliclaw.db`）。没有云端账号，没有服务器聚合，没有任何第三方中介
> - **你的密钥，你做主** — LLM 调用用你自己的 API Key，B 站身份用你自己的 Cookie，随时可以吊销
> - **独一无二的画像** — 每份 Agent 的 MBTI、认知风格、深层需求、猜测兴趣都只从你自己的行为里长出来，和任何其他 OpenBiliClaw 用户零重叠，也无法复制或转让
> - **完全可控** — 打开 `data/` 目录就能看到所有原始数据，任何时候都可以导出、修改或整盘清除。没有任何远程开关能改动你的实例
>
> 每一条推荐，都是"只懂你一个人"的 AI 给出的。

## ✨ 核心特性

- 🧠 **深度用户理解** — 五层网状记忆架构（事件→偏好→觉察→洞察→灵魂），从心理学角度理解你，推断 MBTI、认知风格和深层需求
- 🔍 **多策略内容发现** — 搜索、关联链、趋势、跨域探索四大策略协同，均衡配额分配，像资深 B 站用户一样帮你找好内容
- 🔮 **兴趣猜测与探索** — 基于心理学桥接逻辑，主动猜测你可能感兴趣但从未接触的领域，打破信息茧房
- 💬 **有温度的推荐** — 不是"因为你看过类似视频"，而是像朋友一样解释为什么你会喜欢
- 🎯 **智能多样性** — 来源均衡、主题去重、跨领域覆盖，确保每次推荐都有惊喜
- 🔄 **持续学习** — 苏格拉底式对话 + 行为分析，不断深化对你的理解
- 🔧 **Skill 系统** — 可扩展的技能架构，支持自定义发现策略
- 🔒 **完全私有 · 专属单用户** — 所有画像、记忆、数据留在你本地；LLM 用你自己的 key；每个实例都是只为一个人构建的独一无二的 Agent

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
└── tests/                     # 测试 (497+)
```

## 🚀 快速开始

### ⚡ Quick Install

**终端一条命令(推荐):**

```bash
curl -fsSL https://raw.githubusercontent.com/OpenBiliClaw/OpenBiliClaw/main/scripts/install.sh | bash
```

**复制粘贴给 AI 智能体(Claude Code / Codex CLI / OpenClaw / Cursor 等):**

```text
请按照 https://raw.githubusercontent.com/OpenBiliClaw/OpenBiliClaw/main/docs/agent-install.md 的说明帮我部署 OpenBiliClaw 后端(务必用 Bash 的 curl 下载这个文档,不要用 WebFetch — 会丢关键指令)
```

支持 macOS / Linux / WSL2(Windows 请先装 WSL2)。依赖只有 `git` 和 `python3`(3.11+)。`agent-install.md` 会让智能体跑上面那条 `install.sh` 一键命令,脚本会自动克隆仓库、在 `~/workspace/OpenBiliClaw` / `~/OpenBiliClaw` / `~/projects/OpenBiliClaw` / `~/code/OpenBiliClaw` 里发现已有安装并复用 LLM API Key 和 B 站 Cookie、起后端、做健康检查,最后打印一个自包含的 Status / Missing / Next action 状态块给智能体消费。

> 人类维护者可以参考 [docs/agent-install.md](docs/agent-install.md)(给智能体看的精简契约)和 [docs/agent-deployment.md](docs/agent-deployment.md)(详细排查说明)。

### 手动安装

```bash
# 克隆项目
git clone https://github.com/OpenBiliClaw/OpenBiliClaw.git
cd OpenBiliClaw

# 使用 uv (推荐)
uv sync

# 或使用 pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 手动配置

```bash
# 复制配置模板
cp config.example.toml config.toml

# 编辑配置（设置 LLM API Key 等）
vim config.toml
```

### 运行

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

### Docker 部署

> 📦 也支持 Docker 一键部署，详见 [Docker 部署指南](docs/docker-deployment.md)

## 🛠️ 技术栈

| 模块 | 技术 |
|------|------|
| 后端 | Python 3.11+ |
| 浏览器插件 | TypeScript + Chrome Extension (Manifest V3) |
| LLM | 多模型支持 (Gemini / DeepSeek / OpenAI / Claude / 本地模型) |
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
