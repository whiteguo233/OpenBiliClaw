<div align="center">

# 🦀 OpenBiliClaw

**Your personal AI companion for Bilibili — discovers content you'll love but can't find on your own**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

English | [中文](README.md)

</div>

---

OpenBiliClaw is an open-source AI Agent for personalized content recommendation on [Bilibili](https://www.bilibili.com). It's not a cold recommendation algorithm — it's like a friend who truly understands you: who you are, why you enjoy certain content, and proactively discovers things on Bilibili that you'd love but could never find on your own.

> 🔐 **Strictly single-user · fully local · yours and yours alone**
>
> OpenBiliClaw is not a SaaS and not a multi-tenant platform. Each user installs a private instance that builds an understanding of one person — you — and never mixes your profile, memories, or recommendation signals with anyone else's.
>
> - **Your data stays on your disk** — The soul profile, five-layer memory, dialogue history, and Bilibili behaviour logs all live in a local SQLite file (`data/openbiliclaw.db`). No cloud accounts, no server-side aggregation, no third-party brokers.
> - **Your keys, your call** — LLM calls use your own API key, Bilibili auth uses your own cookie, and you can revoke both at any moment.
> - **A profile that can't be duplicated** — Each Agent's MBTI, cognitive style, deep needs, and speculative interests are generated from your behaviour alone, with zero overlap with any other OpenBiliClaw user, and cannot be copied or transferred.
> - **Fully controllable** — Open the `data/` directory to inspect every piece of raw data, and export, modify, or wipe it whenever you want. No remote switch can change your instance.
>
> Every recommendation comes from an AI that knows exactly one person: you.

## ✨ Key Features

- 🧠 **Deep User Understanding** — Five-layer memory architecture (Event → Preference → Awareness → Insight → Soul) that understands you from a psychological perspective, inferring MBTI, cognitive style, and deep needs
- 🔍 **Multi-Strategy Discovery** — Four coordinated strategies (Search, Trending, Related Chain, Cross-domain Explore) with fair quota distribution, working like an expert Bilibili user finding content for you
- 🔮 **Speculative Interest Exploration** — Uses psychological bridging logic to proactively guess domains you might enjoy but have never explored, breaking the filter bubble
- 💬 **Warm Recommendations** — Not "because you watched similar videos", but friend-like explanations of why you'd enjoy something
- 🎯 **Smart Diversity** — Source balancing, topic deduplication, cross-domain coverage — every recommendation batch brings surprises
- 🔄 **Continuous Learning** — Socratic dialogue + behavioral analysis, constantly deepening its understanding of you
- 🔧 **Skill System** — Extensible skill architecture supporting custom discovery strategies
- 🔒 **Fully private · single-user by design** — All profiles, memories, and data stay on your machine; LLM calls use your own key; each install is a unique instance built for one person

## 🏛️ Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   Chrome Extension                   │
│         (Behavior Collection · Recs · Chat)          │
└────────────────────────┬────────────────────────────┘
                         │ REST API
┌────────────────────────▼────────────────────────────┐
│                 Agent Orchestration                   │
│            (Skill System · Dialogue Mgmt)            │
├─────────┬──────────┬───────────┬────────────────────┤
│  Soul   │ Memory   │ Discovery │  Recommendation    │
│  Engine │ System   │  Engine   │     Engine          │
│(Profile)│(5-Layer) │(4-Strategy│   (Expression)     │
├─────────┴──────────┴───────────┴────────────────────┤
│        LLM Adapters  ·  Bilibili API  ·  SQLite     │
└─────────────────────────────────────────────────────┘
```

### Content Discovery Engine

Four strategies work in coordination, each with independent API quota:

| Strategy | Description | Quota |
|----------|-------------|-------|
| **Search** | Generates queries from interests + speculative interests | Fair share |
| **Trending** | Popular content from multiple Bilibili ranking categories | Fair share |
| **Related Chain** | Expands from seed videos along recommendation chains | Fair share |
| **Explore** | LLM-driven cross-domain exploration | Fair share |

Results go through multi-dimensional diversity selection: per-source reservation → topic deduplication → style balancing → ceiling caps, ensuring broad coverage in final recommendations.

### Soul Engine

Infers from user behavior:
- **Personality Portrait** — Natural language user profile
- **MBTI** — Four dimensions with confidence scores
- **Cognitive Style** — Information processing preferences
- **Deep Needs** — Psychological content drivers
- **Speculative Interests** — System-predicted potential interest domains (e.g., molecular gastronomy, architectural aesthetics, watchmaking...)

## 🏗️ Project Structure

```
OpenBiliClaw/
├── src/openbiliclaw/          # Python backend core
│   ├── agent/                 # Agent orchestration & Skill system
│   ├── soul/                  # Soul Engine (profiling · MBTI · interest speculation)
│   ├── memory/                # Multi-layer memory system
│   ├── discovery/             # Discovery engine (4 strategies · quota balancing · diversity)
│   ├── recommendation/        # Recommendation & expression engine
│   ├── bilibili/              # Bilibili API layer (WBI signing · rate control)
│   ├── llm/                   # Multi-model LLM adapters
│   └── storage/               # Data storage layer
├── extension/                 # Chrome browser extension
├── skills/                    # Built-in Skill definitions
├── docs/                      # Documentation
└── tests/                     # Tests (497+)
```

## 🚀 Quick Start

### ⚡ Quick Install

**One terminal command (recommended):**

```bash
curl -fsSL https://raw.githubusercontent.com/OpenBiliClaw/OpenBiliClaw/main/scripts/install.sh | bash
```

**Paste to an AI coding agent (Claude Code / Codex CLI / OpenClaw / Cursor etc.):**

```text
Please follow https://raw.githubusercontent.com/OpenBiliClaw/OpenBiliClaw/main/docs/agent-install.md to deploy the OpenBiliClaw backend for me (use Bash `curl` to fetch the document, NOT WebFetch — WebFetch summarises markdown and drops critical commands).
```

Works on macOS, Linux and WSL2 (native Windows is not supported — install WSL2). The only prerequisites are `git` and `python3` (3.11+). The linked `agent-install.md` tells the agent to run the one-line `install.sh` above, which then auto-clones the repo, discovers any existing install under `~/workspace/OpenBiliClaw`, `~/OpenBiliClaw`, `~/projects/OpenBiliClaw` or `~/code/OpenBiliClaw` and reuses its LLM API keys + Bilibili cookie, starts the backend, runs a health check, and prints a self-contained **Status / Missing / Next action** block for the agent to consume.

> Human reference: [docs/agent-install.md](docs/agent-install.md) (short agent-facing contract) and [docs/agent-deployment.md](docs/agent-deployment.md) (long-form troubleshooting).

### Manual installation

```bash
# Clone
git clone https://github.com/OpenBiliClaw/OpenBiliClaw.git
cd OpenBiliClaw

# Using uv (recommended)
uv sync

# Or using pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Manual configuration

```bash
# Copy config template
cp config.example.toml config.toml

# Edit config (set LLM API keys, etc.)
vim config.toml
```

### Run

```bash
# One-command init (fetch history · build profile · first discovery)
openbiliclaw init

# Manual content discovery
openbiliclaw discover

# Get recommendations
openbiliclaw recommend

# View user profile
openbiliclaw profile
```

### Docker Deployment

> 📦 Docker deployment is also supported. See the [Docker Deployment Guide](docs/docker-deployment.md) for details.

## 🛠️ Tech Stack

| Module | Technology |
|--------|-----------|
| Backend | Python 3.11+ |
| Browser Extension | TypeScript + Chrome Extension (Manifest V3) |
| LLM | Multi-model support (Gemini / DeepSeek / OpenAI / Claude / Local models) |
| Bilibili API | Custom client (WBI signing · v_voucher auto-recovery · rate control) |
| Storage | SQLite + Embedding vector index |
| Agent Framework | Lightweight custom framework |

## 📖 Documentation

- [Documentation Hub](docs/index.md) — All-in-one entry point
- [Project Spec](docs/spec.md) — Complete design & planning
- [Architecture](docs/architecture.md) — System architecture deep dive
- [Memory Design](docs/memory-design.md) — Multi-layer memory architecture
- [Discovery Engine](docs/modules/discovery.md) — 4-strategy discovery + diversity selection
- [Soul Engine](docs/modules/soul.md) — Deep profiling + MBTI + interest speculation
- [CLI Reference](docs/modules/cli.md) · [Config Reference](docs/modules/config.md)
- [Contributing Guide](docs/contributing.md)

## 🤝 Contributing

Contributions welcome! See the [Contributing Guide](docs/contributing.md) to get started.

## 📄 License

[MIT](LICENSE)
