<div align="center">

# 🦀 OpenBiliClaw

**A general-purpose personalized content discovery Agent — runs locally, understands you across platforms, built only for you**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![LINUX DO](https://img.shields.io/badge/LINUX_DO-Community-black?style=flat-square&logo=linux)](https://linux.do/)
[![Discussion](https://img.shields.io/badge/LINUX_DO-Discussion-orange?style=flat-square&logo=discourse)](https://linux.do/t/topic/1978894)

English | [中文](README.md)

</div>

> The name comes from Bilibili (`Bili` = Bilibili, `Claw` = "the claw that grabs content for you") — the project started as a Bilibili-only tool. Since v0.3.0 it has evolved into a general cross-platform Agent: Bilibili / Xiaohongshu / generic Web adapters all live in production, with more platforms on the roadmap.

---

## 📌 v0.3.0 Highlights (2026-04-28)

- **🌐 General multi-source architecture in production** — evolved from a Bilibili-only tool into a general-purpose content Agent; Xiaohongshu and generic-Web adapters shipped
- **🔌 Local embedding fallback** — optional Ollama + bge-m3, no extra API key needed for similarity computation (CPU-only, works on Mac/Win/Linux)
- **⚡ "Reshuffle" 5x faster** — popup reshuffle from 2.6s → 0.6s; rapid clicks no longer feel laggy
- **🎯 Cross-source topic dedup** — any single topic capped at ≤10% of the candidate pool; no more "all AI all day"

Full changelog: [docs/changelog.md](docs/changelog.md).

---

## Why OpenBiliClaw?

Recommendation systems are essentially a **middleman** — the platform sits between millions of videos and millions of users, matching and distributing content at scale. Modern systems are far more sophisticated than "just optimizing CTR": they jointly weigh click-through rate, completion rate, like/coin probability, dwell time, user retention, creator ecosystem health, ad revenue, and a dozen other objectives, compressing them into a single weighted ranking score. Sounds scientific, but here's the catch: **the weights are set by the platform, and the optimization targets ultimately serve the platform** — user satisfaction is valued as a means to retention and monetization, not as an end in itself. You think you're choosing content, but really the middleman decides what you get to see. The result: recommendations look more and more like what you've already watched, and the occasional surprise is pure luck.

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

1. Open [OpenBiliClaw Releases](https://github.com/whiteguo233/OpenBiliClaw/releases) and find the latest `extension-v*` release
2. Download `openbiliclaw-extension-v*.zip` from that release
3. Open `chrome://extensions/`, enable "Developer mode" in the top right
4. Drag the downloaded `.zip` file into the page to install

> Developers can also `cd extension && npm install && npm run package` to build from source.

#### Important: log in to **every source you want to use**, in the same browser the extension is installed in

OpenBiliClaw doesn't farm credentials — it reuses **your** current browser sessions to discover content cross-platform. So after installing the extension, log in to every source you care about **in the same browser**:

| Source | How to log in | What you lose if you don't |
|---|---|---|
| **Bilibili** | Just log in normally at https://www.bilibili.com (the v0.3.12+ extension auto-syncs the cookie to the backend) | The backend can't fetch your watch history / favorites / following → your soul profile won't reflect your real interests; recommendations degrade to public trending |
| **Xiaohongshu** | Log in normally at https://www.xiaohongshu.com | The backend never crawls Xiaohongshu directly — **all discovery + detail fetches happen through your extension in hidden tabs**. No login = no Xiaohongshu content at all |
| Generic web sources | Log in normally on that site | Same as above |

> 💡 **Strongly recommended for Xiaohongshu: use a CDP-mode Chrome to reuse the login session** (avoids anti-scraping). Launch a separate-profile Chrome with `--remote-debugging-port=9222`, manually log in once, then set `[sources.browser] cdp_url = "http://localhost:9222"` in `config.toml`. See [config reference](docs/modules/config.md#sourcesbrowser).

### ⚡ Step 2: Deploy the Backend

**Recommended: paste to an AI coding agent for one-click deploy** (works with Claude Code / Codex CLI / Cursor etc.):

> 📌 **Prerequisite — you need an AI coding agent first.** If you don't have one, pick any:
> - [Claude Code](https://docs.anthropic.com/claude/docs/claude-code) — Anthropic's official CLI
> - [Codex CLI](https://github.com/openai/codex) — OpenAI's official CLI
> - [Cursor](https://cursor.com) / [Windsurf](https://codeium.com/windsurf) — AI-native IDEs
>
> If installing one of these is more friction than it's worth, jump to "Or: run the one-liner install script" below — it does the same thing without an AI in the loop.

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md to deploy the OpenBiliClaw backend for me (use Bash `curl` to fetch the document, NOT WebFetch — WebFetch summarises markdown and drops critical commands).
```

The AI clones the repo locally, installs dependencies, starts the backend, runs a health check, then **asks you four questions, each with a sensible default**: which LLM to use, which embedding service to use, how to provide the B站 cookie, and whether Xiaohongshu likes/favorites may be used in the initial profile. Embedding defaults to local Ollama bge-m3 (free + offline + no quota cost), while Xiaohongshu data is opt-in only. Finally it auto-runs `init` (fetch history → build soul profile → first discovery pass). Fully transparent. **This is the recommended path for most users — zero friction.**

**Or: have the AI agent deploy with Docker** (good if you have Docker Desktop; v0.3.11+ ships an Ollama embedding sidecar by default):

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md to deploy the OpenBiliClaw backend via Docker Compose (use Bash `curl` to fetch the document, NOT WebFetch).
```

**Or: run the one-liner installer yourself** (the same script the AI uses, no agent required):

macOS / Linux / WSL2 (Bash):

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

Native Windows (PowerShell — no Docker, no WSL2 required):

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

> The leading `[Net.ServicePointManager]...Tls12` lets PowerShell 5.1 (the default on Windows 10/11) successfully negotiate with GitHub. GitHub no longer accepts TLS 1.0/1.1 and PS 5.1 picks those by default. Users on PowerShell 7 can drop the prefix.

Prerequisites: `git` and `python3` (3.11+; on Windows the `py` launcher works). The scripts auto-clone the repo, install dependencies, start the backend, run a health check, and print the exact follow-up questions for LLM, embedding, Bilibili cookie, and Xiaohongshu opt-in. First-time init runs automatically only after those choices and credentials are explicit.

> 💡 **On Windows?** Since v0.3.4 the PowerShell installer fully supports native Windows — no Docker / WSL2 needed. You can still use the Docker path above if you already have Docker Desktop installed.

> 🧠 **Optional: local embedding fallback (no API key required)** — install Ollama once:
> Mac `brew install ollama && ollama serve &`, Windows from [ollama.com/download](https://ollama.com/download), Linux `curl -fsSL https://ollama.com/install.sh \| sh && ollama serve &`.
> Then run `uv run openbiliclaw setup-embedding` — the wizard pulls `bge-m3` (~568MB, CPU only) and writes the config. Useful when your remote embedding quota is exhausted, you're offline, or you just don't want to add another API key.

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

# Optional: enable local Ollama as embedding fallback (no extra API key)
openbiliclaw setup-embedding

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

## 🤖 Integrate with OpenClaw / AI Coding Agents

This repo ships a [workspace skill](skills/openbiliclaw-adapter/SKILL.md). Point any skill-aware AI coding agent (OpenClaw / Claude Code / Codex CLI / Cursor, etc.) at this checkout and it can drive your local OpenBiliClaw directly.

### What you get after integration

- ✨ **Proactive recommendations** — the system continuously discovers content in the background; when it finds a high-scoring surprise, it pushes to OpenClaw via WebSocket — **you don't have to ask**
- 🔮 **Proactive interest probing** — the system guesses you might be into a new domain, generates a hypothesis and a question, and has OpenClaw come ask you "does this direction resonate?" — your answer automatically refines the profile
- 💬 **Socratic dialogue** — not just interest confirmation; OpenClaw can have deep conversations: probing motivations, proposing hypotheses, confirming understanding — the more you talk, the better it knows you
- 📖 **Read the current soul profile** — MBTI, core traits, deep needs, interest domains
- 🎯 **Fetch personalized recommendations on demand** — with explanations, confidence scores, and topic labels
- 💬 **Write feedback back into the learning loop** — `like` / `dislike` / `comment` instantly update the profile and pool scoring
- 🔄 **Sync Bilibili account signals** — pull history / favorites / following and feed them into the memory system

### One-sentence integration prompt

Paste the following into OpenClaw (or Claude Code / Codex CLI / Cursor) — it will read the guide and wire everything up:

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/openclaw-quickstart.md to integrate this repository into OpenClaw (use Bash `curl` to fetch the document, NOT WebFetch — WebFetch summarises markdown and drops critical commands).
```

### Usage examples

After integration, it's not just "you ask, it answers" — **the system comes to you**. Here are the two core scenarios:

#### Scenario 1: System proactively pushes a surprise recommendation

OpenClaw is running `listen` in the background. After a refresh cycle, the system finds a high-scoring piece of content:

> **OpenClaw** (receives WebSocket `delight.candidate`, comes to you proactively):
>
> > Just found something you might unexpectedly enjoy:
> > **"Why You Can't Actually Lie Flat Even Back in a Small Town"** — the Minsky moment of county economies. You've been deep in geopolitics and macro debt; this one dissects how land finance and the debt cycle mesh together. `[BV1KMwuzdEcB]`
> >
> > Want to check it out?
>
> **You**: "That one nailed it."
>
> **OpenClaw** (internally runs `submit-feedback --recommendation-id 4268 --feedback-type like`)

You never asked for a recommendation — the system surfaced it on its own.

#### Scenario 2: System proactively asks about your interests

OpenClaw receives an `interest.probe` event (or pulls `next-probe`) and finds a speculative-interest hypothesis waiting for confirmation:

> **OpenClaw** (comes to you proactively):
>
> > I've been picking up signals that you might be into **architectural aesthetics** (e.g., parametric design, concrete minimalism) — you've been watching a lot of content about structure and space lately. Does this direction resonate with you?
>
> **You**: "Yeah, I've been looking at a lot of parametric design stuff. Tadao Ando's exposed concrete aesthetic really gets me."
>
> **OpenClaw** (internally runs `chat --message "Yeah, I've been looking at…"`, returns a Socratic follow-up):
>
> > Here's my hypothesis — you're not just interested in buildings, you're drawn to how minimal elements create maximum tension. Ando's approach to concrete is the same aesthetic drive as your obsession with "making the gears mesh" when you analyze LLM architectures. Am I close?

The dialogue automatically feeds back into the soul profile — the next discovery cycle will incorporate architectural aesthetics as a real interest and start searching in that direction.

#### Scenario 3: You can also just ask for recommendations

Of course, the traditional "you ask → it answers" flow works too:

> **You**: "Recommend three Bilibili videos worth watching today."
>
> **OpenClaw** (internally runs `recommend --limit 3`, formats and replies)

The whole loop stays local — OpenClaw just calls the CLI bridge; your profile and data never leave the SQLite file on your disk.

> 📖 Full command reference and troubleshooting: [OpenClaw Integration Guide](docs/openclaw-quickstart.md).

## ✨ Key Features

- 🧠 **Five-Layer Soul Profile** — Event → Preference → Awareness → Insight → Soul, inferring MBTI, cognitive style, and deep needs — like a psychologist understanding you
- 🔮 **Speculative Interest System** — Uses psychological bridging logic to guess unexplored domains you might love; promotes correct guesses, retires wrong ones, continuously breaking the filter bubble
- 🌐 **Cross-Platform Sources** — Started on Bilibili, now extended to Xiaohongshu and generic Web; the architecture is built to keep adding more platforms. Your interests no longer get siloed
- 🔍 **Multi-Source Discovery Strategies** — Bilibili four strategies (Search · Related Chain · Trending · Cross-domain Explore) + Xiaohongshu safe discovery (passive collection · keyword search · creator subscription · init-profile import), coordinated cross-platform
- 🎯 **Smart Diversity** — PoolCurator five-dimension scoring + cross-source/round topic quota (any topic ≤10% of pool) + share-aware pool trimming that protects smaller sources; goodbye to "all AI all day"
- ⚡ **Instant "Reshuffle"** — popup reshuffle ~0.6s (down from 2.6s in v0.3.0); rapid clicks stay snappy
- 💬 **Warm Recommendations** — Not "because you watched similar videos", but friend-like explanations of why you'd enjoy something
- 🔄 **Continuous Learning** — Socratic dialogue + behavioral analysis + instant feedback, understands you better over time
- 🧩 **Chrome Extension** — Side panel for recommendations, cross-site behavior collection (Bilibili + Xiaohongshu), chat, and cognition update cards — install and go
- 🔬 **Self-Optimizing Eval Loops** — Five modules each have an LLM-as-judge SGD/RL loop that automatically improves prompt quality over rounds — no manual tuning needed
- 🔒 **Fully Private** — All data in local SQLite; LLM calls use your own key; each instance is built for exactly one person
- 🔌 **Local Embedding Fallback** — Optional Ollama + bge-m3, no extra embedding API key required for similarity computation (CPU-only, runs on Mac/Win/Linux)
- 🔧 **Fully Controllable** — Swap LLMs per module, edit your profile directly, write custom Skills to extend discovery

## 🏛️ Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   Chrome Extension                   │
│ (Behavior · Recs · Chat · Cookie Sync · XHS Init)       │
└────────────────────────┬────────────────────────────┘
                         │ REST API / WebSocket cookie request
┌────────────────────────▼────────────────────────────┐
│                 Agent Orchestration                   │
│            (Skill System · Dialogue Mgmt)            │
├─────────┬──────────┬───────────┬────────────────────┤
│  Soul   │ Memory   │ Discovery │  Recommendation    │
│  Engine │ System   │  Engine   │     Engine          │
│(Profile)│(5-Layer) │(4-Strategy│   (Expression)     │
├─────────┴──────────┴───────────┴────────────────────┤
│ LLM Adapters · Bilibili API · Extension Proxy · SQLite│
└─────────────────────────────────────────────────────┘
```

### Content Discovery Engine

Four Bilibili strategies work in coordination, each with independent API quota, and the source layer also accepts Xiaohongshu extension-proxy signals:

| Strategy | Description | Quota |
|----------|-------------|-------|
| **Search** | Generates queries from interests + speculative interests | Fair share |
| **Trending** | Popular content from multiple Bilibili ranking categories | Fair share |
| **Related Chain** | Expands from seed videos along recommendation chains | Fair share |
| **Explore** | LLM-driven cross-domain exploration | Fair share |

Results go through multi-dimensional diversity selection: source-family reservation (the four Bilibili strategies plus one unified `xiaohongshu` family) → topic deduplication → style balancing → ceiling caps, ensuring broad coverage in final recommendations.

For first-run profiling, `openbiliclaw init` can also enqueue an XHS `bootstrap_profile` task. The extension opens Xiaohongshu in the user's logged-in browser session; explicit scrolling tasks open `/explore` in the foreground and click the page's own "Me" profile entry instead of directly jumping to the profile URL. It then parses rendered profile state / DOM for saved / liked notes, and only imports Xiaohongshu-page history when the site exposes an explicit history/footprint state. Explicit scrolling tasks return `partial` batches as new notes appear, then finish with a final result. The backend converts those notes into normal `favorite / like / view` events and still does not crawl or log into Xiaohongshu directly.

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
│   ├── sources/               # Source adapters and XHS task bridge
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
| Xiaohongshu | Extension DOM/state extraction + task dispatch; scrolling init imports open `/explore` in the foreground, click the page's profile entry, then use bounded scrolling and partial batches; no backend crawling |
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

## 📜 Release History

| Version | Date | Key changes |
|---|---|---|
| **v0.3.64** | 2026-05-06 | XHS bootstrap fetch ceiling 50→**300** per scope / scroll rounds 3→15. `openbiliclaw init` now pulls up to 300 saves/likes per scope (was 50). The scroll executor early-exits after 5 stagnant rounds with no new notes, so users with light XHS histories pay no extra cost while heavy users get a one-shot deep backfill that actually reflects their long-term taste. `OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS` / `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS` env overrides still honored. **No extension repackage required.** |
| **v0.3.63** | 2026-05-06 | Global LLM priority queue: popup-visible `write_expression` (priority=1) no longer FIFO-blocks behind background `delight_score` sweeps (priority=2) — pool expression backfill now lands as soon as the user opens the popup. `BackgroundTaskRegistry`: when config is hot-reloaded at runtime, every detached task (per-strategy precompute / prewarm / per-event trigger) is cancelled within 1.5s, so the new runtime never races the old one for SQLite writes or LLM tokens. |
| **v0.3.62** | 2026-05-05 | Three architectural lock splits (`_precompute_lock` split into `_expression_lock` + `_delight_lock` so popup-visible expression no longer waits behind delight scoring); global `_refresh_lock` skip-if-busy gate prevents multiple refresh entry points from stacking; DB write retry budget tightened from 5×0.1s to 8×0.02s (worst-case sync block 500ms → 160ms). |
| **v0.3.57** | 2026-05-05 | Popup recommendation copy never falls back to the placeholder template again. XHS notes authored by the logged-in user no longer leak into the recommendation pool (every ingest path now captures `self_info`; startup hook purges existing pollution). Daemon retries history fetch within ~30s of cookie sync arriving instead of locking the 6h throttle for an unauthenticated first tick. **Requires extension v0.3.10 in lockstep.** |
| **[v0.3.26](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.26)** | 2026-05-02 | New LLM billing module: every successful call writes one row to the `llm_usage` table; `openbiliclaw cost` CLI prints daily/by-provider spend. `config.example.toml` defaults switched to cost-friendly values (`reasoning_effort=""` thinking off, `discovery_cron 8h`) — fresh installs target ≈¥0.5/day. |
| [v0.3.25](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.25) | 2026-05-02 | Discovery LLM eval `batch_size` 10→30 (amortizes the 3500-token system prompt across one call instead of three → -54% input cost), `max_tokens` 8192→16384; refresh `_requested_refresh_limit` now scales per-strategy ask to actual pool gap (gap=20 → 15 per strategy instead of 30) → -50-77% eval calls. |
| [v0.3.24](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.24) | 2026-05-02 | Cross-source event format unification: Bilibili / Xiaohongshu / extension click / feedback all funnel through `event_format.build_event()` and emit a standardized dict carrying a Chinese natural-language `context`. `_summarize_history` exposes a `contexts` list; preference / awareness / soul prompts add rules pointing the LLM at it. Fixes a DB double-encoding bug that triple-escaped context strings in LLM prompts. |
| [v0.3.23](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.23) | 2026-05-02 | xhs `bootstrap_profile` scrolling tasks now run in foreground tabs (background tabs render only a shallow wrapper on Xiaohongshu so the masonry/waterfall lazy-load never fires); scroll-target detection prefers feed/waterfall/masonry containers and skips zero-height wrappers; profile state parser fills in `displayTitle` / `cover.urlDefault`. |
| [v0.3.22](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.22) | 2026-05-01 | Fix `openbiliclaw init` so Xiaohongshu data actually reaches the soul profile: enqueue/collect API split (8s blocking wait → 30s parallel-with-Bilibili-fetches), `max_scroll_rounds` default 0→3, five completion states (ok/empty/timeout/failed/skipped) each get a clear Chinese feedback line. |
| [v0.3.21](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.21) | 2026-05-01 | Aligns the v0.3.20 UX changes onto the Docker / Windows PowerShell / direct-CLI install paths: `docker-deployment.md` main menu now leads with DeepSeek and demotes the gateway to "Advanced"; `install.ps1` mirrors `install.sh`'s cookie-only-green and REUSE_FROM warning; `cli.py` `_LLM_MENU` reordered + embedding wizard rewritten with the v0.3.20 default-recommendation shape. |
| **[v0.3.20](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.20)** | 2026-05-01 | Install-flow UX fixes + embedding fallback chain: silent-failure bug when Claude / DeepSeek / OpenRouter is the primary LLM and embedding "follows" it — `LLMProvider.supports_embedding` flag drives a fallback chain (ollama → gemini → openai) instead of returning None · `--provider openai` without `--llm-base-url` now clears any stale gateway URL written by a previous run · agent-install.md trims the user's main menu to 3 LLM options (gateway moved to Advanced) · embedding question redesigned with a clear default + tradeoff explanation (recommended: local Ollama bge-m3 — free, offline; alternative: cloud Gemini for higher recall on multilingual / long-form content) · install.sh status block shows green "backend ready — waiting for browser extension" instead of yellow "partial / missing" when only the B站 cookie is pending · README adds an AI-agent prerequisite callout |
| [v0.3.19](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.19) | 2026-05-01 | `openbiliclaw init` now best-effort mixes Xiaohongshu saved / liked / explicit page-history signals into the first profile. The extension runs `bootstrap_profile` in the user's logged-in Xiaohongshu session; scrolling tasks open `/explore` in the foreground and click the page's own profile entry before using `partial` batches. The backend converts notes to normal `favorite / like / view` events without directly crawling Xiaohongshu. |
| **[v0.3.18](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.18)** | 2026-04-30 | Promotes `franchise_key` to a first-class column on `content_cache`, populated directly by the LLM at evaluation time. Downstream curator dislike propagation and `/api/recommendations` IP dedup now read from the real column instead of the title heuristic that v0.3.17 briefly tried. The hardcoded alias list is gone. |
| [v0.3.17](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.17) | 2026-04-30 | Fixes a recommendation pipeline IP over-generalisation bug ("5 Genshin clips in one popup"): adds a heuristic franchise extractor; `/api/recommendations` now caps each franchise at 2 per response window; disliking one Genshin video soft-down-weights all same-franchise candidates instead of just blocking that exact bvid |
| [v0.3.16](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.16) | 2026-04-30 | README backend-install order reshuffled: one-liner / Docker / direct script come first, the unsigned desktop package is moved into a `<details>` block at the end · adds a "log into every source you want to use" pre-install section explaining why Xiaohongshu specifically requires being logged in in the same browser the extension is installed (CDP mode strongly recommended) |
| [v0.3.15](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.15) | 2026-04-30 | Round-up of Windows native-install pitfalls: CLI now forces stdout to UTF-8 on launch (no more `UnicodeEncodeError` on GBK consoles when emoji prints) · install.ps1's `python -c f"..."` rewritten as `print(a, b)` to dodge a PS 5.1 quoting bug · agent-install.md warns AI agents that `bash` on Windows often resolves to the WSL launcher · **fixes a registry bug where Ollama, registered only for embedding, was incorrectly used as a chat-completion fallback, causing `All providers failed (openai, ollama)` when the primary cloud LLM hit a transient error** |
| [v0.3.14](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.14) | 2026-04-30 | Fixes a Windows GBK-locale bug where `/api/delight/pending-batch`, `/api/activity-feed`, etc. returned 500 on first hit: `MemoryLayer.load()/save()` and `bilibili.auth` cookie I/O now pin `encoding="utf-8"` instead of relying on the platform default. Includes a regression test that monkeypatches `builtins.open` to simulate Chinese Windows. |
| [v0.3.13](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.13) | 2026-04-30 | Every install path now leads with "install the extension to auto-sync the cookie" instead of pushing the F12 dance: install.sh / install.ps1 status block, agent-install.md AI-agent contract, the CLI wizard's `_interactive_auth_setup`, docker-deployment.md, and openclaw-quickstart.md all updated. F12 demoted to a fallback. |
| [v0.3.12](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.12) | 2026-04-30 | The browser extension now auto-syncs your Bilibili cookie to the backend — no more F12 dance. The extension reads the live cookie via `chrome.cookies` and POSTs it to a new `/api/bilibili/cookie` endpoint that validates against B站 nav, persists, hot-reloads the runtime, and broadcasts a WebSocket event. Cookie refreshes auto-resync via `chrome.cookies.onChanged`. |
| [v0.3.11](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.11) | 2026-04-30 | Docker mode now ships an Ollama embedding sidecar by default (auto-pulls bge-m3, named-volume persisted) · `docker_runtime.py` seeds `[llm.embedding] provider=ollama` from env on first boot · CLI wizard (direct `openbiliclaw init`) also auto-installs Ollama |
| [v0.3.10](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.10) | 2026-04-30 | When Ollama is picked for chat or embedding, the installer now auto-installs Ollama (brew on macOS / winget on Windows / install.sh on Linux), starts the daemon, and pulls the requested models. No more "I picked Ollama but it doesn't work because nothing is installed" |
| [v0.3.9](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.9) | 2026-04-30 | One-liner installer now works on Windows PowerShell 5.1 (the default on Windows 10/11): TLS 1.2 prefix added to the install command, fixed `??` PS 7-only syntax inside install.ps1, in-script TLS 1.2 fallback for git/uv/pip calls |
| [v0.3.8](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.8) | 2026-04-30 | `openbiliclaw init` now prints an upfront "expected 2–5 min" header + per-stage time estimates so users don't think the silent LLM step is hung |
| [v0.3.7](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.7) | 2026-04-30 | One-line install **auto-runs `openbiliclaw init`** once credentials are filled in (pulls history / builds soul profile / runs first discovery), so the user doesn't have to do an extra manual step · agent-install.md Hard Rule flipped: run init by default · agent_bootstrap.py auto-init now handles Windows + Docker correctly |
| [v0.3.6](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.6) | 2026-04-30 | Install wizard rewritten end-to-end for normal users: Ollama is now the default first choice · "OpenAI official" and "OpenAI-compatible self-hosted gateway" are split into separate menu entries · embedding question is its own clearly-explained step · Bilibili cookie prompt now teaches the F12 → Network steps |
| [v0.3.5](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.5) | 2026-04-29 | 4-phase install wizard (base_url / triplet / embedding 4-way / per-module override) · clears `openai = protocol family` ambiguity · `agent_bootstrap.py` gains 7 new flags |
| [v0.3.4](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.4) | 2026-04-29 | Native-Windows one-liner installer (PowerShell `install.ps1`, no Docker/WSL2) · `agent_bootstrap.py` Windows adaptation (taskkill / netstat-ano) |
| **[v0.3.0](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/backend-v0.3.0)** | 2026-04-28 | General-purpose multi-source architecture (Xiaohongshu / Web adapters in production) · local Ollama embedding fallback · reshuffle 5x faster · cross-source topic quota |
| [v0.2.1](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/v0.2.1) | 2026-04-17 | OpenClaw integration (Socratic chat + interest probes) · Bilibili API resilience hardening |
| [v0.2.0](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/v0.2.0) | 2026-04-16 | macOS .app launch fix · multi-objective recommendation critique · pool hard cap · five-dimension PoolCurator |
| [v0.1.0](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/v0.1.0) | 2026-04-13 | Initial release — end-to-end soul / discovery / recommendation pipeline |

Full milestone history: [docs/changelog.md](docs/changelog.md) · All releases: [GitHub Releases](https://github.com/whiteguo233/OpenBiliClaw/releases)

## 🗺️ Roadmap

OpenBiliClaw aims to be your **personalized entry point to the entire web**. Started on Bilibili, v0.3.0 shipped Xiaohongshu and generic-Web adapters; next:

- **More content sources** — Zhihu, V2EX, Douyin, Weibo, various BBS / forums; each platform is a `SourceAdapter` and the architecture is proven extensible
- **Cross-platform interest fusion** — your mechanical-keyboard interest from Bilibili + your coffee-gear interest from Xiaohongshu = one complete you. Profile fusion stops your interests from being fragmented across silos
- **Smarter cross-source discovery** — "you started following coffee gear on Xiaohongshu, here's a hand-drip documentary on Bilibili you might love"
- **Community ecosystem** — user-defined SourceAdapters, shared discovery strategies, contributed platform adapters

## 🤝 Contributing

Contributions welcome! See the [Contributing Guide](docs/contributing.md) to get started.

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=whiteguo233/OpenBiliClaw&type=Date)](https://www.star-history.com/#whiteguo233/OpenBiliClaw&Date)

## 📄 License

[MIT](LICENSE)
