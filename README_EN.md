<div align="center">

# 🦀 OpenBiliClaw

**An open-source alternative to Bilibili's recommendation algorithm — runs on your machine, understands only you**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

English | [中文](README.md)

</div>

---

## Why OpenBiliClaw?

Bilibili's recommendation engine does "people who watched A also watched B" — it pushes you into an ever-narrowing filter bubble.

**OpenBiliClaw is fundamentally different.** It's a locally-running AI Agent that doesn't care what everyone else watches. Instead, it understands **who you are**:

### 🧠 Understands *why* you like things, not just *what* you've watched

It infers your MBTI, cognitive style, and deep psychological needs from your behaviour, building a five-layer soul profile (Event → Preference → Awareness → Insight → Soul). It's not matching video tags — it's understanding you as a person.

### 🔮 Actively breaks your filter bubble

This is the core differentiator: the system **guesses domains you might enjoy but have never explored**. Someone into mechanical watches might love architectural aesthetics; a quantum physics viewer might resonate with philosophy — it uses psychological bridging logic to proactively explore, promotes correct guesses to real interests, and quietly retires wrong ones.

### 🔒 100% local, 100% yours

All data lives in a single SQLite file on your disk. LLM calls use your own API key. No cloud, no accounts, no one else can see your profile. How this Agent grows is entirely your call — send feedback, chat with it, swap LLMs, edit the database, whatever you want.

> 💡 **How it compares**
>
> | | Bilibili Official | Keyword Filter Plugins | OpenBiliClaw |
> |---|---|---|---|
> | Recommendation logic | Collaborative filtering | Tag matching | Psychological profiling + 5-layer memory |
> | Filter bubble | Gets narrower | Doesn't address it | Speculative interests actively break it |
> | Data ownership | Platform-owned | Usually cloud | 100% local |
> | Explains why | "Guess you'll like" | None | Friend-like explanations |
> | Customizable | No | Low | Swap LLMs / edit profile / write Skills |

## 🚀 Quick Start

### 🧩 Step 1: Install the Chrome Extension

The extension is your main interface — it shows recommendations in a Bilibili side panel, collects behavior, and lets you chat with the agent.

1. Download the latest [`openbiliclaw-extension.zip`](https://github.com/whiteguo233/OpenBiliClaw/releases/latest)
2. Open `chrome://extensions/`, enable "Developer mode" in the top right
3. Drag the downloaded `.zip` file into the page to install

> Developers can also `cd extension && npm install && npm run package` to build from source.

### ⚡ Step 2: Deploy the Backend

**⭐ Paste to an AI coding agent for one-click deploy (recommended — works with Claude Code / Codex CLI / Cursor etc.):**

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md to deploy the OpenBiliClaw backend for me (use Bash `curl` to fetch the document, NOT WebFetch — WebFetch summarises markdown and drops critical commands).
```

**⭐ Have an AI agent deploy with Docker (recommended if you have Docker Desktop):**

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md to deploy the OpenBiliClaw backend via Docker Compose (use Bash `curl` to fetch the document, NOT WebFetch).
```

**One terminal command:**

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

Works on macOS, Linux and WSL2. The only prerequisites are `git` and `python3` (3.11+). The script auto-clones the repo, installs dependencies, starts the backend, runs a health check, and prompts you to choose an LLM provider (OpenAI / Gemini / DeepSeek / Claude etc.) and fill in the corresponding API key and Bilibili cookie. Once credentials are set, it automatically runs first-time init (fetches history, builds your soul profile, fills the recommendation pool) so you're ready to go immediately.

> 💡 **On Windows?** If you already have Docker Desktop, use the Docker method above — it works out of the box. Otherwise, install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) first, then use the terminal command.

<details>
<summary>Manual installation / configuration / browser extension</summary>

> Human reference: [docs/agent-install.md](docs/agent-install.md) (short agent-facing contract) and [docs/agent-deployment.md](docs/agent-deployment.md) (long-form troubleshooting).

#### Manual installation

```bash
# Clone
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw

# Using uv (recommended)
uv sync

# Or using pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

#### Manual configuration

```bash
# Copy config template
cp config.example.toml config.toml

# Edit config (set LLM API keys, etc.)
vim config.toml
```

#### Run

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

#### Docker Deployment

> 📦 Docker deployment is also supported. See the [Docker Deployment Guide](docs/docker-deployment.md) for details.

</details>

## ✨ Key Features

- 🧠 **Five-Layer Soul Profile** — Event → Preference → Awareness → Insight → Soul, inferring MBTI, cognitive style, and deep needs — like a psychologist understanding you
- 🔮 **Speculative Interest System** — Uses psychological bridging logic to guess unexplored domains you might love; promotes correct guesses, retires wrong ones, continuously breaking the filter bubble
- 🔍 **Four Discovery Strategies** — Search, Related Chain, Trending, and Cross-domain Explore working in coordination with fair quotas, like an expert Bilibili user finding content for you
- 🎯 **Smart Diversity** — PoolCurator scores on five dimensions (relevance · freshness · topic fatigue · source monotony · serendipity), ensuring every batch has surprises instead of more of the same
- 💬 **Warm Recommendations** — Not "because you watched similar videos", but friend-like explanations of why you'd enjoy something
- 🔄 **Continuous Learning** — Socratic dialogue + behavioral analysis + instant feedback, understands you better over time
- 🧩 **Chrome Extension** — Side panel for recommendations, real-time behavior collection, chat, and cognition update cards — install and go
- 🔬 **Self-Optimizing Eval Loops** — Five modules each have an LLM-as-judge SGD/RL loop that automatically improves prompt quality over rounds — no manual tuning needed
- 🔒 **Fully Private** — All data in local SQLite; LLM calls use your own key; each instance is built for exactly one person
- 🔧 **Fully Controllable** — Swap LLMs per module, edit your profile directly, write custom Skills to extend discovery

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
└── tests/                     # Tests (650+)
```

## 🛠️ Tech Stack

| Module | Technology |
|--------|-----------|
| Backend | Python 3.11+ |
| Browser Extension | TypeScript + Chrome Extension (Manifest V3) |
| LLM | Built-in Gemini / DeepSeek / OpenAI / Claude / OpenRouter / Ollama; any OpenAI-compatible endpoint works via custom base_url |
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
