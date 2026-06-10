<div align="center">

# 🦀 OpenBiliClaw

**A general-purpose personalized content discovery Agent — runs locally, understands you across platforms, built only for you**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![LINUX DO](https://img.shields.io/badge/LINUX_DO-Community-black?style=flat-square&logo=linux)](https://linux.do/)
[![Discussion](https://img.shields.io/badge/LINUX_DO-Discussion-orange?style=flat-square&logo=discourse)](https://linux.do/t/topic/1978894)
[![Chrome Web Store](https://img.shields.io/chrome-web-store/v/cdfjfkdjjhdaccbldipkjhpibnfbiamg?style=flat-square&label=Chrome%20Web%20Store&logo=googlechrome&logoColor=white&color=4285F4)](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg)

[Homepage](https://whiteguo233.github.io/OpenBiliClaw/) | English | [中文](README.md)

</div>

## OpenBiliClaw in 10 Seconds

A local-first AI discovery agent that learns your taste across Bilibili, Xiaohongshu, Douyin, YouTube, X and the open web — without handing your profile to another platform.

| Cross-platform | Local-first | Trainable |
|---|---|---|
| Bilibili / Xiaohongshu / Douyin / YouTube / X / Web | Data stays in your local SQLite by default | Likes, dislikes, and chat feedback shape future recommendations |

<p align="center">
  <a href="https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg"><b>Install the browser extension</b></a>
  ·
  <a href="#quick-start"><b>Deploy the local backend with an AI coding agent</b></a>
</p>

<p align="center">
  <sub><a href="https://github.com/whiteguo233/OpenBiliClaw">Star the project if you like the direction</a>.</sub>
</p>

<p align="center">
  <img src="docs/images/hero-demo-en.gif" width="820" alt="OpenBiliClaw local-first cross-platform AI discovery agent demo: platform signals, local backend, taste profile, reasoned cards, and feedback loop" />
</p>

## Quick Start

Most users only need these four steps. Firefox, Docker, and manual setup paths are preserved later in [Setup Details](#setup-details).

1. **Install the extension** — recommended: download the latest `extension-v*` zip from [Releases](https://github.com/whiteguo233/OpenBiliClaw/releases) and load it (always newest); or one-click from the [Chrome Web Store](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg) (auto-updates, but the listed version can lag behind Releases due to review delays).
2. **Deploy the backend (two ways — pick one, both recommended)**:
   - 🖥️ **Download the desktop installer (easiest)**: grab the macOS `.dmg` / Windows `.exe` from [Releases](https://github.com/whiteguo233/OpenBiliClaw/releases), install, and launch — it bundles local embedding and lives in the menu bar / system tray. It's an **unsigned experimental pre-release**, so the first launch needs a system-prompt bypass; see [Setup Details](#setup-details).
   - 🤖 **Let an AI coding agent deploy it (pick this to customize / edit the source)**: paste this prompt into Claude Code, Codex CLI, Cursor, Windsurf, or another coding agent.

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md to deploy the OpenBiliClaw backend for me (use Bash `curl` to fetch the document, NOT WebFetch — WebFetch summarises markdown and drops critical commands).
```

3. **Log in to content platforms in the same browser**. Start with [Bilibili](https://www.bilibili.com), then opt in to [Xiaohongshu](https://www.xiaohongshu.com) / [Douyin](https://www.douyin.com) / [YouTube](https://www.youtube.com) / [X](https://x.com) when you want more signals.
4. **Open the desktop or mobile Web UI**. Use `http://127.0.0.1:8420/web` on the same machine, or scan the extension QR code for `http://<your-LAN-IP>:8420/m/` on your phone.

## Why OpenBiliClaw?

> The name comes from Bilibili (`Bili` = Bilibili, `Claw` = "the claw that grabs content for you") — the project started as a Bilibili-only tool. Since v0.3.0 it has evolved into a general cross-platform Agent: Bilibili / Xiaohongshu / Douyin / YouTube / X init signals, Douyin search / hot / feed plus X server-side discovery, and generic Web sources all live in production, with more platforms on the roadmap.

Recommendation systems are essentially a **middleman** — the platform sits between millions of videos and millions of users, matching and distributing content at scale. Modern systems are far more sophisticated than "just optimizing CTR": they jointly weigh click-through rate, completion rate, like/coin probability, dwell time, user retention, creator ecosystem health, ad revenue, and a dozen other objectives, compressing them into a single weighted ranking score. Sounds scientific, but here's the catch: **the weights are set by the platform, and the optimization targets ultimately serve the platform** — user satisfaction is valued as a means to retention and monetization, not as an end in itself. You think you're choosing content, but really the middleman decides what you get to see. The result: recommendations look more and more like what you've already watched, and the occasional surprise is pure luck.

**OpenBiliClaw is fundamentally different.** It's a locally-running AI Agent that doesn't care what everyone else watches. Instead, it understands **who you are**:

### 🧠 Understands *why* you like things, not just *what* you've watched

It infers your MBTI, cognitive style, and deep psychological needs from your behaviour, building a five-layer soul profile (Event → Preference → Awareness → Insight → Soul). It's not matching video tags — it's understanding you as a person.

### 🔮 Actively breaks your filter bubble

This is the core differentiator: the system **guesses domains you might enjoy but have never explored**. Someone into mechanical watches might love architectural aesthetics; a quantum physics viewer might resonate with philosophy — it uses psychological bridging logic to proactively explore, promotes correct guesses to real interests, and quietly retires wrong ones.

### 🔒 100% local, 100% yours

All data lives in a single SQLite file on your disk. LLM calls use your own API key by default, with an experimental option to reuse local Codex CLI ChatGPT OAuth credentials. No cloud, no accounts, no one else can see your profile. How this Agent grows is entirely your call — send feedback, chat with it, swap LLMs, edit the database, whatever you want.

> 💡 **How it compares**
>
> | | Bilibili Official | Keyword Filter Plugins | OpenBiliClaw |
> |---|---|---|---|
> | Recommendation logic | Collaborative filtering | Tag matching | Psychological profiling + 5-layer memory |
> | Content sources | Single platform | Single platform | Cross-platform: Bilibili · Xiaohongshu · Douyin · YouTube · X · more |
> | Filter bubble | Gets narrower | Doesn't address it | Speculative interests actively break it |
> | Data ownership | Platform-owned | Usually cloud | 100% local |
> | Explains why | "Guess you'll like" | None | Friend-like explanations |
> | Customizable | No | Low | Swap LLMs / edit profile / write Skills |

## 📸 Feature Preview

Three core surfaces: the browser extension handles in-page interaction and login sessions, the Desktop Web (`/web`) gives you a big-screen recommendation home, and the Mobile Web (`/m`) is built for phones. Both web surfaces only call your local API — cookie sync and platform tasks still run through the extension.

<table>
  <tr>
    <td align="center" width="25%">
      <img src="docs/images/screenshot-recommend.png" width="200" /><br/>
      <b>Smart Recommendations</b><br/>
      <sub>Friend-like explanations of why you'd enjoy it</sub>
    </td>
    <td align="center" width="25%">
      <img src="docs/images/screenshot-profile-portrait.png" width="200" /><br/>
      <b>Soul Profile</b><br/>
      <sub>Deep personality analysis in natural language</sub>
    </td>
    <td align="center" width="25%">
      <img src="docs/images/screenshot-profile-traits.png" width="200" /><br/>
      <b>Structured Traits</b><br/>
      <sub>MBTI · core traits · deep needs</sub>
    </td>
    <td align="center" width="25%">
      <img src="docs/images/screenshot-chat.png" width="200" /><br/>
      <b>Chat Tuning</b><br/>
      <sub>Tell it what you want to see</sub>
    </td>
  </tr>
</table>

### 🖥️ Desktop Web Preview

After starting the backend, open `http://127.0.0.1:8420/web` (or just `http://127.0.0.1:8420/`, which redirects automatically) for a full-screen recommendation dashboard.

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/images/desktop-home.png" width="480" /><br/>
      <b>Desktop Home</b><br/>
      <sub>Delight hero · recommendation grid · friend-like reasons</sub>
    </td>
    <td align="center" width="50%">
      <img src="docs/images/desktop-cards.png" width="480" /><br/>
      <b>Recommendation Card Grid</b><br/>
      <sub>Cover + reason · like / skip / watch later / favorite / chat</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="docs/images/desktop-profile.png" width="480" /><br/>
      <b>Profile + Live Dashboard</b><br/>
      <sub>Sidebar runtime board + activity · personality sketch · core traits · MBTI</sub>
    </td>
  </tr>
</table>

### 📱 Mobile Web Preview

<table>
  <tr>
    <td align="center" width="33%">
      <img src="docs/images/mobile-recommend.png" width="210" /><br/>
      <b>Recommendations</b><br/>
      <sub>Delight + pool status · friend-like reason</sub><br/>
      <sub>View / like / later / save / not interested / chat</sub>
    </td>
    <td align="center" width="33%">
      <img src="docs/images/mobile-profile.png" width="210" /><br/>
      <b>Profile</b><br/>
      <sub>Personality sketch · core traits · deep needs · MBTI</sub>
    </td>
    <td align="center" width="33%">
      <img src="docs/images/mobile-chat.png" width="210" /><br/>
      <b>Chat</b><br/>
      <sub>Shared main chat history with the extension</sub>
    </td>
  </tr>
</table>

<details>
<summary>More screenshots</summary>

<table>
  <tr>
    <td align="center" width="33%">
      <img src="docs/images/screenshot-recommend-feedback.png" width="200" /><br/>
      <b>Recommendation Feedback</b><br/>
      <sub>Like / more like this / less / not interested</sub>
    </td>
    <td align="center" width="33%">
      <img src="docs/images/screenshot-profile-values.png" width="200" /><br/>
      <b>Values & Interests</b><br/>
      <sub>Inner drivers · speculative interest directions</sub>
    </td>
    <td align="center" width="33%">
      <img src="docs/images/screenshot-profile-style.png" width="200" /><br/>
      <b>Cognitive Style</b><br/>
      <sub>Information processing · content taste</sub>
    </td>
  </tr>
</table>

</details>

## Recent Updates

Latest: **v0.3.115: auto-update unblocked (2026-06-10)**. Full changelog: [docs/changelog.md](docs/changelog.md).

- **Auto-update actually updates now** — fixed stale release lockfiles permanently tripping the dirty-worktree guard on every git install, with zero feedback.
- **Update status visible in settings** — a status line under the auto-update toggle shows current/latest version, blocking reason, and last check time.
- **Desktop packages are honest** — frozen desktop installs can't git-self-update; the toggle is now disabled with a hint to install a newer package instead of silently doing nothing.
- **Existing installs: one-time unlock** — run `git checkout -- uv.lock && git pull` once in the install dir (or re-run the one-line installer) to restore auto-update.

## Community

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/images/user-community-qrcode.png" width="200" alt="QQ user community QR code" /><br/>
      <b>QQ Community</b>
    </td>
    <td align="center" width="50%">
      <img src="docs/images/wechat-user-community-qrcode.jpg" width="200" alt="WeChat user community QR code" /><br/>
      <b>WeChat Community</b><br/>
      <sub>The QR code is valid for 7 days and will be refreshed after it expires.</sub>
    </td>
  </tr>
</table>

## Setup Details

For most users, setup is four steps: install the extension, ask an AI coding agent to deploy the backend, log in to the content platforms in the same browser, and optionally open the Mobile Web app from your phone.

### 1. Install the browser extension

The extension is the main interface. It shows the sidebar on Bilibili, Xiaohongshu, Douyin, YouTube, and X, records your feedback, and lets the local backend reuse your browser session safely.

Built on Manifest V3, the extension works in any Chrome-compatible browser — **Chrome, Edge, Brave, Arc, Vivaldi, Opera**, and more.

**Recommended · download the latest build from Releases** (gets the newest features and fixes — the Chrome Web Store listing usually lags by a few days to a couple of weeks due to review scheduling):

1. Open [OpenBiliClaw Releases](https://github.com/whiteguo233/OpenBiliClaw/releases) and find the latest `extension-v*`
2. Chrome / Edge / Brave users download `openbiliclaw-extension-v*.zip`; Firefox users download `openbiliclaw-extension-v*-firefox.zip`
3. Open the extensions page (Chrome: `chrome://extensions/` · Edge: `edge://extensions/` · Brave: `brave://extensions/`), enable "Developer mode" in the top right
4. Drag the downloaded `.zip` file into the page to install

**Convenient · one-click from the Chrome Web Store** (the browser keeps it auto-updated — best if you don't want to update manually; downside: the version can lag behind Releases):

> 👉 **[Install OpenBiliClaw on the Chrome Web Store](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg)** — click "Add to Chrome".

Extension updates depend on the install channel: Chrome Web Store / Edge Add-ons / AMO builds are updated by the browser; GitHub zip, developer-mode, and Firefox temporary installs must download the new zip and reload it manually. The backend "auto update" switch only updates the local backend source checkout, not the browser extension.

<details>
<summary>Firefox users: download the Firefox package and sideload (Firefox 140+)</summary>

Firefox uses `sidebar_action` instead of Chrome's `sidePanel`, so releases ship a separate `openbiliclaw-extension-v*-firefox.zip`. Download and unzip it, then load it temporarily via `about:debugging`; you can also build the same Firefox package from source:

```bash
unzip openbiliclaw-extension-v*-firefox.zip -d openbiliclaw-firefox

# Or build from source
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw/extension
npm install
npm run build:firefox          # writes dist-firefox/
# or: npm run package:firefox   # also produces openbiliclaw-extension-v*-firefox.zip
```

Then:

1. Open `about:debugging#/runtime/this-firefox`
2. Click "Load Temporary Add-on…"
3. Pick `manifest.json` from the unzipped directory, or `extension/dist-firefox/manifest.json` after a source build

Caveat: temporary add-ons disappear on Firefox restart; signed AMO distribution is still on the roadmap.

</details>

### 2. Deploy the backend (two options)

Most users: the **desktop installer** is the least effort. Want to edit the source, swap LLMs, or customize deeply? Use the **AI one-line deploy**.

#### Option A: Download the desktop installer (experimental, easiest)

Grab the installer for your OS from [Releases](https://github.com/whiteguo233/OpenBiliClaw/releases):

- **macOS**: `.dmg` (separate Apple-silicon `arm64` / Intel `x64` builds) — drag OpenBiliClaw into Applications.
- **Windows**: `.exe` installer — double-click to install.

It bundles local Ollama + `bge-m3` embedding (works out of the box) and lives in the **macOS menu bar / Windows system tray**; right-click for "Open Web UI / View runtime logs / Quit". Data uses the same directory as the AI / script installers: `~/OpenBiliClaw` (macOS / Linux) / `%USERPROFILE%\OpenBiliClaw` (Windows), and survives upgrades and uninstalls. Data from older packaged builds under `~/Library/Application Support/OpenBiliClaw` / `%LOCALAPPDATA%\OpenBiliClaw` is copied back on first launch without overwriting existing files.

> ⚠️ **First launch needs a system-prompt bypass (the app isn't signed / notarized yet)**:
> - **macOS**: drag it into Applications first, then right-click the icon → "Open" → click "Open" again in the dialog; or allow it under "System Settings → Privacy & Security". If macOS still says the app is damaged, confirm the package came from this project's Releases and run:
>
>   ```bash
>   APP="/Applications/OpenBiliClaw.app"
>   xattr -dr com.apple.quarantine "$APP"
>   codesign --force --deep --sign - "$APP"
>   open "$APP"
>   ```
> - **Windows**: on the SmartScreen prompt, click "More info → Run anyway".
>
> This is an **experimental pre-release**: unsigned, rolling with the backend version, best for trying it fast without the command line. To hack on the source, use Option B.

#### Option B: AI one-line deploy (customizable / editable source)

Paste this whole prompt into Claude Code, Codex CLI, Cursor, Windsurf, or another AI coding agent. The parenthetical note is for the agent; you do not need to understand it.

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md to deploy the OpenBiliClaw backend for me (use Bash `curl` to fetch the document, NOT WebFetch — WebFetch summarises markdown and drops critical commands).
```

The agent will clone the repo, install dependencies, start the backend with the LAN-accessible default bind (`0.0.0.0:8420`), run a health check, and ask a few questions with defaults. Before auto-init, it verifies that both the configured LLM provider and embedding service answer real lightweight calls; if either fails, init is blocked until you fix the service. If unsure, pick the default. Xiaohongshu, Douyin, YouTube, and X signals are used in the initial profile only when you explicitly opt in.

Chrome Web Store / AMO builds only declare local-backend permissions, so keep the extension pointed at `127.0.0.1` / `localhost`. To reach the Mobile Web from your phone, start the backend with `openbiliclaw start --host 0.0.0.0 --port 8420` — the extension QR code will prefer your computer's LAN IP. Pointing the extension directly at another LAN machine or a remote domain needs a developer build with the matching host permission, or a future optional-permission toggle.

### 3. Log in to content platforms in the same browser

At minimum, log in to [Bilibili](https://www.bilibili.com). OpenBiliClaw uses it to build the first profile and recommendations. If you want Xiaohongshu, Douyin, YouTube, or X, also log in to [Xiaohongshu](https://www.xiaohongshu.com) / [Douyin](https://www.douyin.com) / [YouTube](https://www.youtube.com) / [X](https://x.com) in the same browser where the extension is installed.

### 4. Open Desktop or Mobile Web

The backend serves both a desktop and a mobile Web UI. Neither syncs cookies or crawls pages — they only call your local API.

```bash
openbiliclaw start
```

- **Desktop**: open `http://127.0.0.1:8420/web` (or `http://127.0.0.1:8420/`, auto-redirects). Two-column editorial layout with recommendations, profile, chat, messages, and settings all on one page.
- **Mobile**: click the phone icon in the extension header to scan the QR code, or type `http://<your-LAN-IP>:8420/m/` manually. Best for browsing recommendations, profile, and chat on your phone.

> During `openbiliclaw init`, you'll be asked whether to allow LAN access (default Y). If you chose N or want to change it later, edit `[api].host` in `config.toml` (`0.0.0.0` = LAN-reachable, `127.0.0.1` = local only).

The app has five bottom tabs: Recommendations, Watch Later, Favorites, Profile, and Chat. Recommendations support reshuffle, load more, like, not interested, watch later, favorite, comments, and contextual chat. Watch Later and Favorites manage your saved lists. Profile shows the personality sketch, core traits, interests, and cognition updates. Chat shares the main chat history with the extension.

<details>
<summary>No AI agent: run the one-line installer yourself</summary>

macOS / Linux / WSL2 (Bash):

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

Native Windows (PowerShell, no Docker or WSL2 required):

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

The script needs `git` and Python 3.11+. It clones the repo, then asks for LLM provider, embedding, Bilibili cookie, Xiaohongshu opt-in, Douyin opt-in, YouTube opt-in, and X opt-in choices in the terminal wizard before installing dependencies or starting the backend. Once the confirmations are complete, it starts the backend, runs the health check, verifies that the LLM provider and embedding service can really respond, then automatically runs init to build the first profile and discovery pool. If unsure, press Enter or choose the default.

</details>

<details>
<summary>Advanced: Docker deployment</summary>

Good if you already have Docker Desktop installed. v0.3.11+ includes an Ollama embedding sidecar.

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md to deploy the OpenBiliClaw backend via Docker Compose (use Bash `curl` to fetch the document, NOT WebFetch).
```

See the [Docker Deployment Guide](docs/docker-deployment.md). The primary Docker path also goes through `agent_bootstrap.py --mode docker`; after LLM, embedding, Bilibili cookie, and source opt-in confirmations it verifies the AI services and then automatically runs init. `docker exec ... openbiliclaw init` remains an advanced manual fallback.

</details>

<details>
<summary>Advanced: multi-source login and plugin path</summary>

OpenBiliClaw does not store your platform passwords or bypass login. It reuses the browser sessions you already control and only fetches content you can see.

| Source | How to log in | What happens if you do not |
|---|---|---|
| **Bilibili** | Log in normally at https://www.bilibili.com in the extension browser | Watch history / favorites / following are unavailable, so the profile is much weaker |
| **Xiaohongshu** | Log in normally at https://www.xiaohongshu.com in the same browser | Xiaohongshu discovery and detail fetches are unavailable |
| **Douyin** | Log in normally at https://www.douyin.com in the same browser | `init --yes-douyin`, `fetch-douyin`, and `discover --source douyin` search / hot / feed may return 0 items |
| **YouTube** | Log in normally at https://www.youtube.com in the same browser | `init --yes-youtube` and `fetch-youtube` may return 0 items; `import-youtube` can still import Google Takeout data |
| **X (Twitter)** | Log in normally at https://x.com in the same browser | `init --yes-x`, `fetch-x`, and X discovery return nothing (server-side replay needs `auth_token`+`ct0`, auto-synced by the extension after login) |

Xiaohongshu, Douyin, and YouTube use Chrome extension tasks, while X uses server-side cookie replay (the extension only syncs the x.com cookie and captures engagement); none of them need an extra CDP debugging Chrome. `[sources.browser].cdp_url` remains available only for generic Web / custom webpage fetching.

</details>

<details>
<summary>Advanced: local embedding / Ollama</summary>

If you do not want a separate embedding API key, or remote embedding quota is an issue, install Ollama once and use local `bge-m3`:

```bash
# macOS
brew install ollama && ollama serve &

# Linux
curl -fsSL https://ollama.com/install.sh | sh && ollama serve &
```

Windows users can install it from [ollama.com/download](https://ollama.com/download). Then run:

```bash
uv run openbiliclaw setup-embedding
```

The wizard pulls `bge-m3` (~568MB, CPU-only is fine) and writes the config.

</details>

<details>
<summary>Advanced: manual installation and discovery debugging</summary>

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

# Optional: enable local Ollama as an independent embedding provider
openbiliclaw setup-embedding

# Manual content discovery
openbiliclaw discover

# Optional: Douyin discovery (requires [sources.douyin]; search / hot / feed use background plugin signing)
openbiliclaw discover --source douyin

# Optional: standalone Douyin search / hot / feed recall debugging
openbiliclaw discover-douyin --keyword mechanical-keyboard --source search,feed --no-cache --no-evaluate

# Get recommendations
openbiliclaw recommend

# View user profile
openbiliclaw profile
```

Developers can also build the extension from source:

```bash
cd extension
npm install
npm run package
```

</details>

## 🤖 Integrate with OpenClaw / AI Coding Agents

This repo ships a [workspace skill](skills/openbiliclaw-adapter/SKILL.md). Point any skill-aware AI coding agent (OpenClaw / Claude Code / Codex CLI / Cursor, etc.) at this checkout and it can drive your local OpenBiliClaw directly.

### What you get after integration

- ✨ **Proactive recommendations** — the system continuously discovers content in the background; when it finds a high-scoring surprise, it pushes to OpenClaw via WebSocket — **you don't have to ask**
- 🔮 **Proactive interest probing** — the system guesses you might be into a new domain, generates a hypothesis and a question, and has OpenClaw come ask you "does this direction resonate?" — your answer automatically refines the profile
- 🧭 **Proactive avoidance probing** — the system can also ask whether a low-quality form, style boundary, or topic shape is something you want to avoid; OpenClaw uses `next-avoidance-probe` / `respond-avoidance-probe`, and nothing is filtered until you confirm it
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
- 🔮 **Challenge Interest Probes** — Uses psychological bridging logic to guess unexplored domains you might love, labels distance as near/lateral/bridge/wildcard, keeps 5 regular near slots plus 3 separate challenge slots, buffers weak positives, and guards against short-term over-amplification
- 🧭 **Avoidance Probe System** — Proactively confirms content forms, low-quality expressions, and style boundaries you may want to avoid; confirmed answers write `disliked_topics`, unconfirmed probes stay out of ranking
- 🌐 **Cross-Platform Sources** — Started on Bilibili, now extended to Xiaohongshu, Douyin, YouTube init signals, Douyin search / hot / feed discovery, X (Twitter) server-side cookie-replay discovery, and generic Web; the architecture is built to keep adding more platforms. Your interests no longer get siloed
- 🔍 **Multi-Source Discovery Strategies** — Bilibili four strategies (Search · Related Chain · Trending · Cross-domain Explore) + Xiaohongshu safe discovery + Douyin plugin-signed search / hot / feed + X search / For-You / followed authors, coordinated cross-platform
- 🎯 **Smart Diversity** — PoolCurator five-dimension scoring + cross-source/round topic quota (any topic ≤10% of pool) + share-aware pool trimming that protects smaller sources; goodbye to "all AI all day"
- ⚡ **Instant "Reshuffle"** — popup reshuffle ~0.6s (down from 2.6s in v0.3.0); rapid clicks stay snappy
- 💬 **Warm Recommendations** — Not "because you watched similar videos", but friend-like explanations of why you'd enjoy something
- 🔄 **Continuous Learning** — Socratic dialogue + behavioral analysis + instant feedback, understands you better over time
- 🧩 **Browser Extension (Chrome / Edge / Brave / Arc and more)** — Side panel for recommendations, cross-site behavior collection (Bilibili + Xiaohongshu + Douyin + YouTube + X), chat, and cognition update cards — install and go
- 🚀 **Guided init in the UI** — No terminal required: the packaged `/setup/` wizard, Desktop Web `/web` uninitialized state, and the extension's "Recommend" tab all show a prerequisite checklist (Bilibili login / LLM / embedding) and a "Start init" button that builds your profile and first content pool in place (the CLI `openbiliclaw init` remains an equivalent entry point)
- 🔬 **Self-Optimizing Eval Loops** — Five modules each have an LLM-as-judge SGD/RL loop that automatically improves prompt quality over rounds — no manual tuning needed
- 🔒 **Fully Private** — All data in local SQLite; LLM calls use your own key; each instance is built for exactly one person
- 🔌 **Local Embedding Provider** — Optional Ollama + bge-m3, no extra embedding API key required for similarity computation (CPU-only, runs on Mac/Win/Linux)
- 🔧 **Fully Controllable** — Swap LLMs per module, edit your profile directly, write custom Skills to extend discovery

## 🏛️ Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   Chrome Extension                   │
│      (Behavior · Recs · Source-Aware Clicks · Chat · Probes) │
│      (Cookies · XHS/DY/YT tasks · init bridge · autostart setting) │
└────────────────────────┬────────────────────────────┘
                         │ REST API / WebSocket (presence + cookies + pool counts + source-aware clicks + probes)
                         │ + Mobile/Desktop Web (/m · /web) · optional [api.auth] password gate (local free / LAN needs password)
┌────────────────────────▼────────────────────────────┐
│                 Agent Orchestration                   │
│       (Skills · Dialogue · Runtime Gate · Account Sync) │
├─────────┬──────────┬───────────┬────────────────────┤
│  Soul   │ Memory   │ Discovery │  Recommendation    │
│  Engine │ System   │  Engine   │     Engine          │
│(Profile+Probe)│(5-Layer+Buffer)│(Eval Pool+Neg.)│(Guarded Mix)│
├─────────┴──────────┴───────────┴────────────────────┤
│ LLM (API Key/Codex OAuth) · Bilibili API · Extension Proxy │
│ Runtime: account sync + producers + probe arbiter + autostart/Ollama │
│ Runtime status: pool_available/raw/pending/eval_count      │
│ SQLite: events · discovery_candidates · content_cache   │
│         recommendations · chat_turns · avoidance_state  │
│ Profile overrides: edits -> profile_overrides.json overlay │
│         (merged at read · rebuild-proof · 3 frontends)   │
└─────────────────────────────────────────────────────┘
```

### Content Discovery Engine

Four Bilibili strategies work in coordination, each with independent API quota, and the source layer also accepts Xiaohongshu extension-proxy signals, YouTube init signals plus a backend-direct YouTube producer, Douyin init signals / plugin-signed search / hot / feed discovery, and X (Twitter) server-side cookie-replay discovery (search / For-You / followed authors):

| Strategy | Description | Quota |
|----------|-------------|-------|
| **Search** | Generates queries from interests + speculative interests | Fair share |
| **Trending** | Popular content from multiple Bilibili ranking categories | Fair share |
| **Related Chain** | Expands from seed videos along recommendation chains | Fair share |
| **Explore** | LLM-driven cross-domain exploration | Fair share |

**Safe data fetching** — Bilibili and generic Web fetch backend-direct (Bilibili via WBI-signed APIs); Xiaohongshu / Douyin / YouTube are read by the browser extension inside your *already-logged-in* pages: init profiling doesn't deep-scroll by default and returns in batches, and the backend never crawls or logs in to those sites itself (YouTube can also import old history via Google Takeout). X is fetched backend-side via read-only server-side cookie replay using the x.com cookie the extension synced (`auth_token` + `ct0`); the extension only syncs the cookie and captures your own engagement. For steady-state refill, Douyin signs requests from a background tab in your logged-in browser without stealing focus, and YouTube is refilled backend-side by platform deficit.

**Unified evaluation** — every source first writes raw candidates to `discovery_candidates`. The backend then claims mixed-source batches and scores them with the Soul profile plus recent negative examples. The "will this user like it?" judgment lives in this shared evaluator, not inside each platform producer.

**Diversity selection** — accepted results then pass through platform-quota reservation → topic dedup → style balancing → **cross-platform interleaving** → count caps, so recommendations never become "all AI all day". Saved platform shares default to Bilibili / Xiaohongshu / Douyin / YouTube / X = 5 / 1 / 1 / 1 / 1, configurable via `[scheduler.pool_source_shares]`; out of the box only Bilibili is enabled and the others must be turned on explicitly.

**Pool counts** — the "swappable" number in the UI only counts `pool_available_count`: candidates with ready copy, a category and an openable link, and no recent-view conflict. Material still being prepared counts as `pool_pending_count`; `pool_pending_eval_count` / `pool_evaluated_pending_count` split out the not-yet-scored and scored-but-not-admitted stages. The extension / Mobile Web / Desktop Web never present pending material as swappable.

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
│   ├── soul/                  # Soul Engine (profiling · MBTI · interest/avoidance probes)
│   ├── memory/                # Multi-layer memory system
│   ├── discovery/             # Discovery engine (strategies · candidate pool · quota balancing · diversity)
│   ├── recommendation/        # Recommendation & expression engine
│   ├── sources/               # Source adapters and XHS/Douyin/YouTube task bridges
│   ├── youtube/               # Google Takeout import parser
│   ├── api/                   # Local FastAPI (config rollback / degraded mode / popup API)
│   ├── runtime/               # Refresh, presence gate, autostart/Ollama, degraded RuntimeContext
│   ├── bilibili/              # Bilibili API layer (WBI signing · rate control)
│   ├── llm/                   # Multi-model LLM adapters + structured JSON tolerance
│   └── storage/               # Data storage layer
├── extension/                 # Chrome browser extension (Bilibili + XHS + Douyin + YouTube + X + autostart/config recovery)
├── skills/                    # Built-in Skill definitions
├── docs/                      # Documentation
└── tests/                     # Tests (1900+)
```

## 🛠️ Tech Stack

| Module | Technology |
|--------|-----------|
| Backend | Python 3.11+ |
| Browser Extension | TypeScript + Chrome Extension (Manifest V3) |
| LLM | Built-in Gemini / DeepSeek / OpenAI / Claude / OpenRouter / Ollama; any OpenAI-compatible endpoint works via custom base_url; OpenAI can experimentally reuse Codex CLI OAuth |
| Bilibili API | Custom client (WBI signing · v_voucher auto-recovery · rate control) |
| Xiaohongshu | Extension DOM/state extraction + task dispatch; scrolling init imports open `/explore` in the foreground, click the page's profile entry, then use bounded scrolling and partial batches; no backend crawling |
| Douyin | Extension DOM + MAIN-world fetch/API harvester + task dispatch; init imports post / favorite / like / follow signals; search / hot / feed discovery use background tabs and the logged-in plugin signer; no backend crawling |
| YouTube | Extension DOM task dispatch reads watch history / subscriptions / likes; Google Takeout can import older data offline |
| X (Twitter) | Server-side cookie replay via lazy-imported `twitter-cli` (optional `openbiliclaw[x]`, read-only); the extension captures your engagement and syncs the x.com cookie; tweets render as text cards |
| Storage | SQLite + Embedding vector index |
| Containerization | Docker Compose (backend) |
| Agent Framework | Lightweight custom framework |

## 📖 Documentation

- [Documentation Hub](docs/index.md) — All-in-one entry point
- [Project Spec](docs/spec.md) — Complete design & planning
- [Architecture](docs/architecture.md) — System architecture deep dive
- [Memory Design](docs/memory-design.md) — Multi-layer memory architecture
- [Discovery Engine](docs/modules/discovery.md) — multi-source discovery + platform mix + diversity selection
- [Soul Engine](docs/modules/soul.md) — Deep profiling + MBTI + interest speculation
- [CLI Reference](docs/modules/cli.md) · [Config Reference](docs/modules/config.md)
- [Contributing Guide](docs/contributing.md)

## 📜 Release History

Latest: **v0.3.115: auto-update unblocked (2026-06-10)**. The recent updates section keeps the current release visible; full history lives in [docs/changelog.md](docs/changelog.md). Extension packages and desktop installers live on [GitHub Releases](https://github.com/whiteguo233/OpenBiliClaw/releases); backend source updates use `backend-v*` tags.

## 🗺️ Roadmap

OpenBiliClaw aims to be your **personalized entry point to the entire web**. Started on Bilibili, it now ships Xiaohongshu, Douyin, and YouTube init signals, Douyin search / hot / feed discovery, X (Twitter) server-side discovery, and a generic-Web adapter; next:

- **More content sources** — Zhihu, V2EX, Weibo, various BBS / forums; each platform is a `SourceAdapter` and the architecture is proven extensible
- **Cross-platform interest fusion** — your mechanical-keyboard interest from Bilibili + your coffee-gear interest from Xiaohongshu + your short-video taste from Douyin likes/favorites + your long-form watching and subscriptions from YouTube + the news you like/bookmark on X = one complete you. Profile fusion stops your interests from being fragmented across silos
- **Smarter cross-source discovery** — "you started following coffee gear on Xiaohongshu, here's a hand-drip documentary on Bilibili you might love"
- **Community ecosystem** — user-defined SourceAdapters, shared discovery strategies, contributed platform adapters

## 🤝 Contributing

Contributions welcome! See the [Contributing Guide](docs/contributing.md) to get started.

## 🙏 Acknowledgements

- Thanks to [@addtion99](https://github.com/addtion99) for proposing configurable browser-extension backend host / port settings and sharing the popup-side implementation idea in [#8](https://github.com/whiteguo233/OpenBiliClaw/pull/8).
- Thanks to [@jiaobenhaimo](https://github.com/jiaobenhaimo) for contributing Safari extension, watch-later bookmarks, YouTube repost detection, and marketing filter designs in [#53](https://github.com/whiteguo233/OpenBiliClaw/pull/53). The OR-join dedup fix and watch-later feature have been merged into main.

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=whiteguo233/OpenBiliClaw&type=Date)](https://www.star-history.com/#whiteguo233/OpenBiliClaw&Date)

## Privacy at a glance

Default data flow: browser extension → your configured local OpenBiliClaw backend → SQLite on your machine. The extension does not send data to servers operated by OpenBiliClaw developers. If you configure a cloud LLM or embedding provider, the relevant content is sent to that provider according to your configuration. See the [Privacy Policy](docs/privacy.md).

## 📄 License

[MIT](LICENSE)
