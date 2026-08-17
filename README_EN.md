<div align="center">

# 🦀 OpenBiliClaw

**A general-purpose personalized content discovery Agent — runs locally, understands you across platforms, built only for you**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/github/v/release/whiteguo233/OpenBiliClaw?filter=openbiliclaw-v*&style=flat-square&label=Release&color=success)](https://github.com/whiteguo233/OpenBiliClaw/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/whiteguo233/OpenBiliClaw/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/whiteguo233/OpenBiliClaw/actions/workflows/ci.yml)
[![Discussion](https://img.shields.io/badge/LINUX_DO-Discussion-orange?style=flat-square&logo=discourse)](https://linux.do/t/topic/1978894)
[![Chrome Web Store](https://img.shields.io/chrome-web-store/v/cdfjfkdjjhdaccbldipkjhpibnfbiamg?style=flat-square&label=Chrome%20Web%20Store&logo=googlechrome&logoColor=white&color=4285F4)](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg)
[![Gitee Mirror](https://img.shields.io/badge/Gitee-Mirror-C71D23?style=flat-square&logo=gitee&logoColor=white)](https://gitee.com/whiteguo233/OpenBiliClaw)

[Homepage](https://whiteguo233.github.io/OpenBiliClaw/) | English | [中文](README.md)

</div>

> ### 🆕 Big update: OpenBiliClaw now runs inside DeepSeek Harness
>
> New **DSH client plugin** — install OpenBiliClaw into [DeepSeek Harness](https://github.com/deepseek-ai/DeepSeek-Harness): a persistent fourth column (Recommendations / Library / Chat / Profile / Settings) in the DSH web GUI, plus 22 Agent Bridge tools so agents can read recommendations, answer probes, and close the learning loop — browse cross-platform personalized content while you work in DSH. → [`github.com/whiteguo233/dsh-openbiliclaw`](https://github.com/whiteguo233/dsh-openbiliclaw)
>
> 📱 Want a native app? The Flutter mobile client (Android / iOS / Web / desktop) lives in the separate repo [`OpenBiliClaw-mobile`](https://github.com/whiteguo233/OpenBiliClaw-mobile): recommendations, chat, profile, favorites / watch-later / 30-day history — all talking to the same local backend.

## OpenBiliClaw in 10 Seconds

A local-first AI discovery agent that learns your taste across Bilibili, Xiaohongshu (RedNote), Douyin, YouTube, X, Zhihu, Reddit, Linux.do, Bangumi, V2EX, Weibo, and the open web — without handing your profile to another platform.

| Cross-platform | Local-first | Trainable |
|---|---|---|
| Bilibili / Xiaohongshu / Douyin / YouTube / X / Zhihu / Reddit / Linux.do / Bangumi / V2EX / Weibo / Web | Data stays in your local SQLite by default | Likes, dislikes, and chat feedback shape future recommendations |

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

Four steps for most users. Firefox, Docker, scripted, and manual setup paths all live in [Setup Details](#setup-details).

1. **Install the extension** — one-click from the [Chrome Web Store](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg) (auto-updates), or download the zip from [Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) for the newest build (the store listing can lag a few days behind).
2. **Install the backend** — grab the desktop installer from the same [Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) (macOS `.dmg` / Windows `.exe`, works out of the box, lives in the menu bar / tray). Each platform ships two variants: the **lean** installer (default; downloads the bge-m3 embedding model on first launch) and the **`-with-embedding`** installer (bge-m3 baked in, ~1.1GB, offline-ready) — pick with-embedding for a poor / offline network, lean otherwise. Or, to customize or edit the source, paste this into Claude Code / Codex CLI / Cursor or another AI coding agent:

   ```text
   Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md to deploy the OpenBiliClaw backend for me (use Bash `curl` to fetch the document, NOT WebFetch — WebFetch summarises markdown and drops critical commands).
   ```

3. **Connect a source** — log in to [Bilibili](https://www.bilibili.com) (the default init source), or choose Xiaohongshu / Douyin / YouTube / X / Zhihu / Reddit / Linux.do / V2EX / Weibo. Linux.do, Bangumi, V2EX, and Weibo support public discovery; signed-in Linux.do, V2EX, and Weibo add read-only personal signals during initialization, Bangumi can initialize from a public username, and Weibo's public path remains anonymous.
4. **Open the UI** — visit `http://127.0.0.1:8420/web`, or scan the extension QR code to open `http://<your-LAN-IP>:8420/m/` on your phone and save it to your home screen. For a native app experience, install the [Flutter client](https://github.com/whiteguo233/OpenBiliClaw-mobile) from its separate repo (Android / iOS / Web / desktop; installers on its [Latest Release](https://github.com/whiteguo233/OpenBiliClaw-mobile/releases/latest)) and point it at the same backend in its settings.

## Why OpenBiliClaw?

> The name comes from Bilibili (`Bili` = Bilibili, `Claw` = "the claw that grabs content for you") — the project started as a Bilibili-only tool. Since v0.3.0 it has evolved into a general cross-platform Agent covering Bilibili / Xiaohongshu / Douyin / YouTube / X / Zhihu / Reddit / Linux.do / Bangumi / V2EX / Weibo and the open web, with more platforms on the roadmap.

Recommendation systems are essentially a **middleman** — the platform sits between millions of videos and millions of users, matching and distributing content at scale. Modern systems are far more sophisticated than "just optimizing CTR": they jointly weigh click-through rate, completion rate, like/coin probability, dwell time, user retention, creator ecosystem health, ad revenue, and a dozen other objectives, compressing them into a single weighted ranking score. Sounds scientific, but here's the catch: **the weights are set by the platform, and the optimization targets ultimately serve the platform** — user satisfaction is valued as a means to retention and monetization, not as an end in itself. You think you're choosing content, but really the middleman decides what you get to see. The result: recommendations look more and more like what you've already watched, and the occasional surprise is pure luck.

**OpenBiliClaw is fundamentally different.** It's a locally-running AI Agent that doesn't care what everyone else watches. Instead, it understands **who you are**:

### 🧠 Understands *why* you like things, not just *what* you've watched

It infers your MBTI, cognitive style, and deep psychological needs from your behaviour, building a five-layer soul profile (Event → Preference → Awareness → Insight → Soul). It's not matching video tags — it's understanding you as a person.

### 🔮 Actively breaks your filter bubble

This is the core differentiator: the system **guesses domains you might enjoy but have never explored**. Someone into mechanical watches might love architectural aesthetics; a quantum physics viewer might resonate with philosophy — it uses psychological bridging logic to proactively explore, promotes correct guesses to real interests, and quietly retires wrong ones.

### 🔒 100% local, 100% yours

Core behavior, recommendation, and dialogue data lives in SQLite on your disk; config, profiles, credentials, and caches also stay in local files. LLM calls use your own API key by default, with an experimental option to reuse local Codex CLI ChatGPT OAuth credentials. There is no OpenBiliClaw-operated cloud account, and no one else can see your profile. How this Agent grows is entirely your call — send feedback, chat with it, swap LLMs, migrate it, or edit the database.

> 💡 **How it compares**
>
> | | Bilibili Official | Keyword Filter Plugins | OpenBiliClaw |
> |---|---|---|---|
> | Recommendation logic | Collaborative filtering | Tag matching | Psychological profiling + 5-layer memory |
> | Content sources | Single platform | Single platform | Cross-platform: Bilibili · Xiaohongshu · Douyin · YouTube · X · Zhihu · Reddit · Linux.do · Bangumi · V2EX · Weibo · more |
> | Filter bubble | Gets narrower | Doesn't address it | Speculative interests actively break it |
> | Data ownership | Platform-owned | Usually cloud | 100% local |
> | Explains why | "Guess you'll like" | None | Friend-like explanations |
> | Customizable | No | Low | Swap LLMs / edit profile / write Skills |

## 📸 Feature Preview

Five core surfaces: the browser extension handles in-page interaction and login sessions, the Desktop Web (`/web`) gives you a big-screen recommendation home, the Mobile Web (`/m`) is built for phones, a native Flutter client ([OpenBiliClaw-mobile](https://github.com/whiteguo233/OpenBiliClaw-mobile), separate repo) covers Android / iOS / Web / desktop, and a [DSH client plugin](https://github.com/whiteguo233/dsh-openbiliclaw) brings the same panels into the DSH web GUI as a fourth column (plus 22 Agent Bridge tools). Every non-extension surface only calls your local API — cookie sync and platform tasks still run through the extension.

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

> 📱 Want a native app? The separate repo [OpenBiliClaw-mobile](https://github.com/whiteguo233/OpenBiliClaw-mobile) (Flutter) ships Android / iOS / Web / Linux / macOS / Windows clients with recommendations, chat, profile, favorites / watch-later / 30-day history, and an inbox — Bilibili covers load straight from the CDN to skip two hops. Grab the signed Android APK or the self-signing iOS IPA from its [Latest Release](https://github.com/whiteguo233/OpenBiliClaw-mobile/releases/latest) (iOS needs re-signing with your own Apple account). Preview build; not yet long-term tested.

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

📌 Latest: **v0.3.207 (2026-08-15)**

- **Faster pool replenishment** — healthy sources keep backfilling the global pool when other source quotas stall, fixing the all-day underfill.
- **Keyless web grounding** — new Bing RSS fallback delivers real search results without Exa / You keys.
- **Direct Exa / You.com APIs** — set `exa_api_key` / `you_api_key` to call them straight from Python, no mcporter needed.
- **More resilient sources** — V2EX missing-CLI and Weibo `upstream_rejected` degrade gracefully instead of spamming tracebacks.

Full changelog: [docs/changelog.md](docs/changelog.md).

## Community

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/images/user-community-qrcode.png" width="200" alt="QQ user community QR code" /><br/>
      <b>QQ Community</b>
    </td>
    <td align="center" width="50%">
      <a href="https://discord.gg/PU6Xgch8yg"><img src="docs/images/discord-community-qrcode.jpg" width="200" alt="Discord community QR code" /></a><br/>
      <b>Discord Community</b><br/>
      <sub>Scan or <a href="https://discord.gg/PU6Xgch8yg">click to join</a> — this invite does not expire.</sub>
    </td>
  </tr>
</table>

## Setup Details

For most users, setup is four steps: install the extension, ask an AI coding agent to deploy the backend, log in to the content platforms in the same browser, and optionally open the Mobile Web app from your phone.

### 1. Install the browser extension

The extension is the main interface. It shows the sidebar on supported sites, records feedback, and runs bounded read-only tasks for sources including Zhihu, Reddit, Linux.do, V2EX, and Weibo. Linux.do, V2EX, and Weibo task tabs are isolated from passive behavior collection; Weibo public discovery still runs independently in the backend.

Built on Manifest V3, the extension works in any Chrome-compatible browser — **Chrome, Edge, Brave, Arc, Vivaldi, Opera**, and more; a **Safari (macOS)** build is also provided. Releases automatically attach `openbiliclaw-extension-v*-safari.dmg` (Developer ID-signed and notarized when Apple credentials are configured, otherwise an ad-hoc experimental build that requires Safari's "Allow Unsigned Extensions"), and you can also convert the local build to an Xcode project via Apple's `safari-web-extension-converter` (see the [Safari build guide](docs/safari-extension-build.md)).

**Recommended · download the latest build from the Latest Release aggregate page** (gets the newest features and fixes — the Chrome Web Store listing usually lags by a few days to a couple of weeks due to review scheduling):

1. Open [OpenBiliClaw Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest), the newest user-facing aggregate `openbiliclaw-v*` release
2. Chrome / Edge / Brave users download `openbiliclaw-extension-v*.zip`; Firefox users install `openbiliclaw-extension-v*-firefox.xpi` when it is present, otherwise download `openbiliclaw-extension-v*-firefox.zip` and load it temporarily through `about:debugging`; Safari (macOS) users download `openbiliclaw-extension-v*-safari.dmg`, launch the app once, then enable OpenBiliClaw in Safari Settings → Extensions
3. Open the extensions page (Chrome: `chrome://extensions/` · Edge: `edge://extensions/` · Brave: `brave://extensions/`), enable "Developer mode" in the top right
4. Chrome / Edge / Brave users drag the downloaded `.zip` file into the page to install; Firefox `.xpi` files install directly, while the temporary zip must be unzipped before loading `manifest.json`

**Convenient · one-click from the Chrome Web Store** (the browser keeps it auto-updated — best if you don't want to update manually; downside: the version can lag behind Releases):

> 👉 **[Install OpenBiliClaw on the Chrome Web Store](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg)** — click "Add to Chrome".

Extension updates depend on the install channel: Chrome Web Store / Edge Add-ons and the Firefox AMO listed build after approval are updated by the browser; GitHub Release Chrome zips / Firefox signed XPIs / Firefox temporary zips / Safari DMGs, developer-mode loads, and Firefox temporary installs must download the new package and reload it manually. Firefox AMO listed review is asynchronous; until the listed version is publicly approved, use the `*-firefox.zip` temporary package from Releases. After approval, Firefox will update the listed install natively. The backend "auto update" switch only updates the local backend source checkout, not the browser extension.

<details>
<summary>Firefox users: regular install and temporary debugging (Firefox 140+)</summary>

Firefox uses `sidebar_action` instead of Chrome's `sidePanel`, so releases ship separate Firefox artifacts:

- `openbiliclaw-extension-v*-firefox.xpi`: signed through Mozilla AMO unlisted signing when AMO signing is enabled and credentials are available, installable directly in regular Firefox Release / Beta.
- `openbiliclaw-extension-v*-firefox.zip`: unsigned development package for `about:debugging` temporary loading or AMO signing input. Installing this zip directly in regular Firefox reports that the add-on could not be verified.

For temporary debugging or source builds:

```bash
unzip openbiliclaw-extension-v*-firefox.zip -d openbiliclaw-firefox

# Or build from source
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw/extension
npm install
npm run build:firefox          # writes dist-firefox/
npm run package:firefox        # also produces unsigned openbiliclaw-extension-v*-firefox.zip
# With AMO credentials configured, sign it into the installable XPI:
# AMO_JWT_ISSUER=... AMO_JWT_SECRET=... npm run sign:firefox:only
```

Then:

1. Open `about:debugging#/runtime/this-firefox`
2. Click "Load Temporary Add-on…"
3. Pick `manifest.json` from the unzipped directory, or `extension/dist-firefox/manifest.json` after a source build

Caveat: temporary add-ons disappear on Firefox restart; regular users should prefer the signed `.xpi` when the release provides one.

</details>

### 2. Deploy the backend (two options)

Most users: the **desktop installer** is the least effort. Want to edit the source, swap LLMs, or customize deeply? Use the **AI one-line deploy**.

#### Option A: Download the desktop installer (experimental, easiest)

Grab the installer for your OS from the `openbiliclaw-v*` aggregate [Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest). The aggregate page shows:

- Current backend source tag: `backend-v*`
- Current extension release: `extension-v*`, with `openbiliclaw-extension-v*.zip` / `openbiliclaw-extension-v*-firefox.zip` (Firefox temporary debugging); AMO signing-enabled releases also include `openbiliclaw-extension-v*-firefox.xpi` (regular Firefox install)
- Current desktop installer release: `desktop-v*`, with available `.dmg` / `.exe` assets when the same-version desktop channel has shipped; missing channels are shown as unpublished instead of being backfilled from a previous release

- **macOS**: download the DMG that matches your Mac: `OpenBiliClaw-macos-v*-arm64.dmg` for Apple silicon, or `OpenBiliClaw-macos-v*-x64.dmg` for Intel when the release provides it. The recommended path is to double-click `安装并启动 Install OpenBiliClaw.command`: it verifies the new bundle, quits the old instance, atomically replaces the app in Applications, and launches the version just installed. Traditional drag-and-drop remains available, but upgrades must quit the old version first and reopen the replacement manually.
- **Windows**: download `OpenBiliClaw-windows-*-Setup.exe` — double-click to install. After a successful install or upgrade, Setup stops the old instance and automatically launches the newly installed version from the installation directory (including silent installs).

It bundles local Ollama + `bge-m3` embedding (works out of the box) plus the default source dependencies, including X's `twitter-cli` and Reddit's `rdt-cli` (Reddit's rdt command backend prefers the connected extension's synced `reddit_session`; `rdt login` remains a manual fallback, and unauthenticated runs fall back to extension tasks). It lives in the **macOS menu bar / Windows system tray**; right-click for "Open Web UI / View runtime logs / Quit". Data uses the same directory as the AI / script installers: `~/OpenBiliClaw` (macOS / Linux) / `%USERPROFILE%\OpenBiliClaw` (Windows), and survives upgrades and uninstalls. Data from older packaged builds under `~/Library/Application Support/OpenBiliClaw` / `%LOCALAPPDATA%\OpenBiliClaw` is copied back on first launch without overwriting existing files. If a broken `config.toml` / `config.local.toml` prevents startup, the desktop package backs the bad file up as `*.invalid`, regenerates the default config, then opens `/setup/` so initialization can run again; `data/` is left untouched.

> ⚠️ **macOS security blocking (the app isn't signed / notarized yet)**:
> - The current Release is ad-hoc signed but not notarized. On first launch, if macOS blocks either the install helper or the app, right-click / Control-click that item → "Open" → click "Open" again in the dialog; or allow it under "System Settings → Privacy & Security" with "Open Anyway".
> - If macOS says "`OpenBiliClaw.app` is damaged and can't be opened", it is usually the download quarantine attribute. After confirming the package came from this project's Releases, run:
>
>   ```bash
>   APP="/Applications/OpenBiliClaw.app"
>   xattr -dr com.apple.quarantine "$APP"
>   ```
>
>   Then open the app again.
> - **Windows**: on the SmartScreen prompt, click "More info → Run anyway".
>
> This is an **experimental pre-release**: unsigned, rolling with the backend version, best for trying it fast without the command line. To hack on the source, use Option B.

#### Option B: AI one-line deploy (customizable / editable source)

Paste this whole prompt into Claude Code, Codex CLI, Cursor, Windsurf, or another AI coding agent. The parenthetical note is for the agent; you do not need to understand it.

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md to deploy the OpenBiliClaw backend for me (use Bash `curl` to fetch the document, NOT WebFetch — WebFetch summarises markdown and drops critical commands).
```

The agent will clone the repo, install dependencies, start the backend with the LAN-accessible default bind (`0.0.0.0:8420`), run a health check, and ask a few questions with defaults. Before auto-init, it verifies that the ordered global LLM instance chain and the independent embedding service answer real lightweight calls; if either fails, init is blocked until you fix the service. Xiaohongshu, Douyin, YouTube, X, Zhihu, Reddit, Linux.do, V2EX, and Weibo signals enter the initial profile only when you opt in. Weibo personal signals require a signed-in Weibo browser session and the extension; public discovery remains anonymous.
The agent will clone the repo, install dependencies, start the backend with the LAN-accessible default bind (`0.0.0.0:8420`), run a health check, and ask a few questions with defaults. Before auto-init, it verifies that the ordered global LLM instance chain and the independent embedding service answer real lightweight calls; if either fails, init is blocked until you fix the service. If unsure, pick the default. Xiaohongshu, Douyin, YouTube, X, Zhihu, Reddit, Linux.do, V2EX, and Weibo signals are used in the initial profile only when you explicitly opt in. Bangumi discovery needs no login; public collections seed the profile only when you enter a public username. Weibo public discovery needs no login, while personal initialization requires a signed-in Weibo browser and extension.

Chrome Web Store / AMO builds only declare local-backend permissions by default. When you select a protocol and enter another LAN or remote endpoint, the browser requests `scheme://host/*`; WebExtension host permissions cannot be port-scoped across browsers, while actual requests remain pinned to the configured port. Public hosts require HTTPS. Enable the default-off device flow first with `ext-key generate` and `ext-key enable`.

With a public DNS name, the shortest path is the [`docker-compose.https.yml`](docker-compose.https.yml) overlay: Caddy obtains and renews the certificate automatically, and desktop, mobile, and the extension share `https://<domain>`. Commands and required access controls are in the [HTTPS deployment guide](docs/https-deployment.md).

### 3. Log in to content platforms in the same browser

By default, log in to [Bilibili](https://www.bilibili.com) and keep Bilibili selected to build the first profile and recommendations. Otherwise select another signed-in source such as Xiaohongshu, Douyin, YouTube, X, Zhihu, Reddit, [Linux.do](https://linux.do), or [V2EX](https://www.v2ex.com), or choose Bangumi with a public username. Keep at least one source that can return profile signals. Signed-out Linux.do / V2EX and Bangumi without identity still support public discovery but cannot initialize a profile alone.

### 4. Open Desktop or Mobile Web

The backend serves both a desktop and a mobile Web UI. Neither syncs cookies or crawls pages — they only call your local API.

```bash
openbiliclaw start
```

- **Desktop**: open `http://127.0.0.1:8420/web` (or `http://127.0.0.1:8420/`, auto-redirects). Two-column editorial layout with recommendations, 30-day history, profile, chat, messages, and settings all on one page.
- **Mobile**: click the phone icon in the extension header to scan the QR code, or type `http://<your-LAN-IP>:8420/m/` manually. Best for browsing recommendations, revisiting 30-day history, profile, and chat on your phone.
- **Native Flutter client**: download the Android APK (`arm64-v8a` for modern devices, `armeabi-v7a` for older ones) or the unsigned iOS IPA (re-sign with your own Apple account) from the [Latest Release](https://github.com/whiteguo233/OpenBiliClaw-mobile/releases/latest), then enter the backend IP / port in the top-right settings (Web / iOS / macOS default to `127.0.0.1:8420`, the Android emulator to `10.0.2.2:8420`, real devices to your computer's LAN IP, and remote deployments to the server IP with the password gate enabled).

> During `openbiliclaw init`, you'll be asked whether to allow LAN access (default Y). If you chose N or want to change it later, edit `[api].host` in `config.toml` (`0.0.0.0` = LAN-reachable over available IPv4 and IPv6, `127.0.0.1` = local only). QR links prefer IPv4 and automatically use a bracketed IPv6 literal when IPv4 is unavailable.

After opening `/m/`, save it as a home-screen shortcut: on iPhone / iPad, use Safari's Share menu and choose "Add to Home Screen"; on Android Chrome / Chromium browsers, use the menu item "Install app" or "Add to Home screen". LAN HTTP may only create a shortcut in some Android browsers; full PWA install prompts are more reliable behind HTTPS in a trusted local setup.

The bottom bar now has four top-level tabs: Recommendations, Content Library, Profile, and Chat. Content Library contains Watch Later, Favorites, and History as child tabs. History pages through the last 30 days as opened, surfaced-but-unopened, and recently removed content; multiple removal contexts stay on one card, and Favorite and Watch Later can be restored independently. Old direct links to the three former tabs migrate to the matching Content Library child.

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

The script needs `git` and Python 3.11+. It clones the repo, then asks for the preferred LLM instance, embedding, Bilibili cookie, and Xiaohongshu / Douyin / YouTube opt-ins before installing dependencies or starting the backend. Once confirmed, it starts the backend, verifies the global LLM instance chain and embedding service, then runs init to build the first profile and discovery pool. X, Zhihu, Reddit, Linux.do, Bangumi, V2EX, and Weibo can be enabled afterward in `/setup/` or settings. Public Linux.do, Bangumi, V2EX, and Weibo discovery needs no login; Weibo personal initialization needs a signed-in Weibo browser and extension, while Bangumi personal initialization needs a public username. If unsure, press Enter or choose the default.

</details>

<details>
<summary>Advanced: Docker deployment</summary>

Good if you already have Docker installed; ships with an Ollama embedding sidecar. The prebuilt image needs no source checkout:

```bash
mkdir -p ~/openbiliclaw && cd ~/openbiliclaw
curl -fsSLO https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docker-compose.prebuilt.yml
docker compose -f docker-compose.prebuilt.yml up -d
# then open http://127.0.0.1:8420/setup/ to finish initialization
```

Or paste this into an AI coding agent for the terminal wizard + auto-init path:

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/docker-deployment.md to deploy the OpenBiliClaw backend via Docker Compose (use Bash `curl` to fetch the document, NOT WebFetch).
```

Source builds, upgrades, and troubleshooting: [Docker Deployment Guide](docs/docker-deployment.md).

</details>

<details>
<summary>Advanced: multi-source login and plugin path</summary>

OpenBiliClaw does not store your platform passwords or bypass login. Login-required sources reuse browser sessions you already control, while anonymous sources read public content only; neither crosses what you are allowed to access.

| Source | How to log in | What happens if you do not |
|---|---|---|
| **Bilibili** | Log in normally at https://www.bilibili.com in the extension browser | Watch history / favorites / following are unavailable, so the profile is much weaker |
| **Xiaohongshu** | Log in normally at https://www.xiaohongshu.com in the same browser | Xiaohongshu discovery and detail fetches are unavailable |
| **Douyin** | Log in normally at https://www.douyin.com in the same browser | `init --yes-douyin`, `fetch-douyin`, and `discover --source douyin` search / hot / feed may return 0 items |
| **YouTube** | Log in normally at https://www.youtube.com in the same browser | `init --yes-youtube` and `fetch-youtube` may return 0 items; `import-youtube` can still import Google Takeout data |
| **X (Twitter)** | Log in normally at https://x.com in the same browser | `init --yes-x`, `fetch-x`, and X discovery return nothing (server-side replay needs `auth_token`+`ct0`, auto-synced by the extension after login) |
| **Zhihu** | Log in normally at https://www.zhihu.com in the same browser | `init --yes-zhihu`, `fetch-zhihu`, `discover --source zhihu`, and `discover-zhihu*` return nothing |
| **Reddit** | Log in normally at https://www.reddit.com in the same browser; the extension syncs `reddit_session` for backend-installed rdt-cli, and `rdt login` is only a fallback when the extension is unavailable | `fetch-reddit --mode bootstrap` returns no init signals; without a synced rdt credential, the rdt path falls back to extension tasks |
| **Linux.do** | Log in normally at https://linux.do in the same browser; public discovery does not require login | Signed out, `fetch-linuxdo` and `init --yes-linuxdo` cannot read bookmarks / likes / read history, while search / hot / feed / creator / related discovery remains available |
| **Bangumi** | No login required; optionally enter a public username for public collections, or a personal token for private ones; the extension only does account identity recognition on bgm.tv / bangumi.tv (no cookies, no browsing capture) | Without a username, Bangumi cannot be the only profile-init source, but anonymous search/ranked/date discovery still works |
| **V2EX** | No login required; optionally configure a PAT; guided init / incremental tasks use the extension to read public rendered fields for topics, replies, favorite topics, and favorite nodes | Anonymous search/node/tab/hot/latest discovery still works without the extension; favorite scopes require an actual logged-in browser session |

Xiaohongshu, Douyin, YouTube, Zhihu, and Linux.do use Chrome extension tasks; Reddit defaults to backend-installed rdt-cli for steady-state discovery and keeps the extension for init signals; X discovery uses server-side cookie replay. None of these read paths needs an extra CDP debugging Chrome. Linux.do requests are same-origin GETs inside real site tabs; `_t` is reduced to a login boolean and neither cookie values nor raw responses are uploaded. Reddit/X, YouTube, Xiaohongshu, Douyin, and Zhihu native-save executors are wired 6/6 and fixture-tested; in the 2026-07-14 real-account regression, every platform's favorite and watch-later/favorite-fallback path finished `synced/already_synced`. Linux.do exposes no native write-back. `[sources.browser].cdp_url` remains available only for generic Web / custom webpage fetching.

</details>

<details>
<summary>Advanced: local embedding / Ollama</summary>

If you do not want a separate embedding API key, or remote embedding quota is an issue, install Ollama once and use local `bge-m3`:

```bash
# macOS
# Install and launch the official Ollama.app; it creates the ollama CLI link.
open https://ollama.com/download/mac

# Linux
curl -fsSL https://ollama.com/install.sh | sh && ollama serve &
```

macOS / Windows users can install the official app from [ollama.com/download](https://ollama.com/download). Start Ollama, then run:

```bash
uv run openbiliclaw setup-embedding
```

The wizard pulls `bge-m3` (~1.1GB, CPU-only is fine) and writes the config.

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

# Optional: Douyin discovery (requires [sources.douyin]; search / hot / feed are triggered from the home page via DOM)
openbiliclaw discover --source douyin

# Optional: read-only Linux.do bookmarks / likes / read-history smoke (does not write memory by default)
openbiliclaw fetch-linuxdo

# Optional: formal Linux.do search / hot / feed / creator / related discovery
openbiliclaw discover-linuxdo --limit 30
# Equivalent: openbiliclaw discover --source linuxdo --limit 30

# Optional: standalone Douyin search / hot / feed recall debugging
openbiliclaw discover-douyin --keyword mechanical-keyboard --source search,feed --no-cache --no-evaluate

# Optional: public Weibo discovery (enable [sources.weibo] first; public reads do not write profile)
openbiliclaw discover --source weibo
openbiliclaw discover-weibo mechanical-keyboard
openbiliclaw discover-weibo-hot
openbiliclaw discover-weibo-creator 1234567890

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

## 🤖 Integrate with OpenClaw / Hermes / WorkBuddy Agents

This repo ships a [workspace skill](skills/openbiliclaw-adapter/SKILL.md) and a versioned, host-neutral Agent Bridge. Point any skill-aware or local-JSON-capable agent (OpenClaw / Hermes / WorkBuddy / Claude Code / Codex CLI / Cursor, etc.) at this checkout and it can drive your local OpenBiliClaw directly.

### What you get after integration

- ✨ **Proactive recommendations** — the system continuously discovers content in the background; when it finds a high-scoring surprise, it pushes to OpenClaw via WebSocket — **you don't have to ask**
- 🔮 **Proactive interest probing** — confirm, reject, defer, or discuss speculative interests
- 🧭 **Proactive avoidance probing** — the same four-state contract for content boundaries; nothing is filtered until you confirm it
- 💬 **Durable Socratic dialogue** — every supported storage backend can return a stable `turn_id` and history for retries and host changes
- 📖 **Read the current soul profile** — MBTI, core traits, deep needs, interest domains
- 🎯 **Fetch multi-source recommendations** — platform scope, reshuffle, append, inventory availability, explanations and content metadata
- 💬 **Write durable feedback back into the learning loop** — recommendation and delight-card actions are idempotent
- 💾 **Local-first saved lists** — favorite/watch-later membership is local; native sync requires explicit authorization
- 🔄 **Sync Bilibili account signals** — pull history / favorites / following and feed them into the memory system

### One-sentence integration prompt

Paste the following into OpenClaw (or Claude Code / Codex CLI / Cursor) — it will read the guide and wire everything up:

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/openclaw-quickstart.md to integrate this repository into the Agent Bridge (target host: OpenClaw; use Bash `curl` to fetch the document, NOT WebFetch — WebFetch summarises markdown and drops critical commands).
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
> **OpenClaw** (internally runs `submit-feedback --recommendation-id 4268 --feedback-type like --request-id feedback-4268-like-1`, reusing that ID on retries)

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

The whole loop stays local — the agent host just calls the CLI bridge; your profile and data never leave the SQLite file on your disk.

> 📖 Full command reference and troubleshooting: [Agent Bridge Integration Guide](docs/openclaw-quickstart.md) and [capability contract](docs/agent-integration.md). Hosts should run `capabilities` at startup instead of caching an old subset.

## ✨ Key Features

- 🧠 **Five-Layer Soul Profile** — Event → Preference → Awareness → Insight → Soul, inferring MBTI, cognitive style, and deep needs ([details](docs/modules/soul.md))
- 🔮 **Interest Probes** — psychological bridging guesses domains you might love but have never explored; right guesses become real interests, wrong ones quietly retire
- 🧭 **Avoidance Probes** — proactively confirms content forms and style boundaries you want to avoid; nothing is filtered until you confirm
- 🌐 **Cross-Platform Sources** — Bilibili / Xiaohongshu / Douyin / YouTube / X / Zhihu / Reddit / Linux.do / Bangumi / V2EX / Weibo / generic Web, so your interests stop being siloed ([details](docs/modules/discovery.md))
- 🎯 **Smart Diversity** — topic quotas + cross-platform interleaving + small-source protection; goodbye to "all AI all day"
- ⚡ **Instant, deduplicated reshuffle** — ~0.6s; current cards, recommendation history, and the durable seen ledger are excluded by default
- 💬 **Warm Recommendations** — friend-like explanations of why you'd enjoy something, not "because you watched similar videos"
- 🔄 **Continuous Learning** — Socratic dialogue + behavioral analysis + instant feedback; it understands you better over time
- ⭐ **Local-First Favorites / Watch Later** — cards save to local SQLite first and auto-sync stays off by default; desktop Web hydrates the sidebar count badges on first load; the 2026-07-14 real-account regression completed both actions across all seven platforms as `synced/already_synced`
- 🕘 **30-Day Content History** — extension, desktop, and mobile share opened, surfaced-but-unopened, and recently removed views; covers are paged and lazy-loaded, and removed local saves can be restored
- 🧩 **Browser Extension** — Chrome / Edge / Brave / Arc / Firefox / Safari; side-panel recommendations + cross-site behavior collection, install and go
- 📱 **Flutter Native Client** — separate repo [OpenBiliClaw-mobile](https://github.com/whiteguo233/OpenBiliClaw-mobile); Android / iOS / Web / Linux / macOS / Windows against the same local backend, with Bilibili covers hitting the CDN directly to skip two hops
- 🚀 **Guided Init in the UI** — the packaged `/setup/` wizard, Desktop Web, and the extension can all initialize with one click; no terminal required
- 📦 **Cross-Machine Migration** — export/import portable config, SQLite, profiles, cookies, and the image cache from Desktop settings; imports are validated and staged, can be inspected or cancelled, then apply on restart with rollback copies. `.obcbackup` contains plaintext secrets but excludes the source machine's API-login password, session-signing secret, and extension device keys
- 🔬 **Self-Optimizing Eval Loops** — five modules each carry an LLM-as-judge loop that improves prompt quality over rounds
- 🔒 **Fully Private** — SQLite, config, profiles, and caches stay local; LLM calls use your own key, and each instance is built for exactly one person
- 🔌 **Local Embedding** — optional Ollama + bge-m3, CPU-only, no extra API key
- 🔧 **Fully Controllable** — create multiple independent channels of the same LLM type and drag global or per-module failover chains; edit your profile or add custom Skills

## 🏛️ Architecture Overview

```text
interactive (dialogue / config probe) ──────────────┐
                                                    ├─ runtime total gate (default 4) ─ ordered instance chain ─ adapter
background ─ background admission (default 3) ──────┘
             ├─ refill: expression > evaluation > supply
             │  ├─ low-stock supply includes explore queries / source extraction
             │  └─ while queued: guarantee 2, may borrow all 3
             │     expression owner: 8 immediate / 3s fixed tail / 60 drain / 30×2 provider
             └─ maintenance: at most 1 while refill waits;
                parked when canonical available = 0

guided init: signals → preferences → full profile commit → discover → evaluate → copy → canonical ready
                                                              └→ optional probes after terminal state

Agent hosts (OpenClaw / Hermes / WorkBuddy)
        → capabilities(agent-bridge/v2) + JSON CLI / skill descriptors
        → integrations.agent alias / integrations.openclaw compatibility adapter
        → runtime / soul / recommendation / saved_sync owners

config recovery draft (normal or degraded; business APIs remain gated)
             ├→ /api/config/probe-service → temporary registry → total gate
             └→ /api/config/discover-models → exact instance GET /models (no write)
                                           → editable model list + local effort advisory
Douyin supply: daemon presence gate (explicit manual calls bypass it) → one shared plugin-cycle budget
              → terminal dy_task → pending_eval; absent means zero enqueue, failures back off
local migration: export → config minus api.auth + online SQLite snapshot + portable files → plaintext .obcbackup
                 import(request_id) → processing(upload/validate) → private stage ↔ status/cancel
                                    ↘ General-open force reconcile; applied prefs once/browser/migration_id
                                    → restart + project/canonical data-dir locks → replace | rollback
durable reply: reply_to_turn_id + fixed time/payload → POST-time frozen binding → pending SQLite → rowid-serial reply worker → visible completion CAS (app-stable dialogue lease)
post-reply learning/object settlement: independent 11-kind typed queue → actual worker + guard
confirmation entry (pending list/cards) → one anchor(kind+ref+generation) → frozen admission / relation matrix
                          ├→ pending≤3 · user no cooldown / system 12h+object 72h · confirmation-first attachment
                          ├→ busy worker: dialogue_busy + Retry-After → waiting UI auto-retry
                          ├→ active confusion: current holder only; hidden once this session has its turn
                          ├→ frozen kind/ref/generation → worker-only apply → event/object/derived/marker → applied
                          │                                                └→ publication-only retry → projection / exact release
                          ├→ one context digest → prompt/history/event/learn/settlement provenance
                          ├→ action local≤1s: completed 200 / blocked 202 → popup/mobile/desktop poll 1/2/5s, ≤30s
                          └→ confusion FIFO≤5 / head fencing / 12h recovery
config save: persist → HTTP 202 queued/apply_revision → latest-wins background apply queue → apply-status / final receipt; data_dir is persisted only and switches after a full restart
config hot reload: accepting drain old worker → atomic pause/revoke → new worker; 25m safety window
realtime: runtime-stream 20s idle heartbeat → transient close shows reconnecting and retries
images: proxy foreground + refresh prefetch → app-stable lane (total 4 / bg 3, fg priority)
                                            → cache-key singleflight → whitelist fetch → atomic cache
```

```
┌────────────────────────────────────────────────┐
│ Browser Extension (Chrome / Firefox / Safari)  │
│  Behavior capture · MAIN-world taps (comment/  │
│  danmaku, xhs strong signal) · Cookie · Tasks  │
└──────────────────────┬─────────────────────────┘
                       │ HTTP default: IPv4 0.0.0.0 + IPv6 [::] → REST / WebSocket
                       │ Optional HTTPS: public Caddy :443 / LAN TLS Proxy :8443 → loopback HTTP → same API
                       │ + Desktop Web (/web) · Mobile Web (/m) · QR LAN-IP
                       │ + ping preflight → /web · /setup · /m → config + in-process recovery
┌──────────────────────▼─────────────────────────┐
│               Agent Orchestration               │
│ Skills · Dialogue · Runtime · 10s undo barrier   │
├─────────┬──────────┬───────────┬───────────────┤
│  Soul   │  Memory  │ Discovery │ Recommendation │
│ Engine  │  System  │Discovery +│     Engine     │
│         │          │ Admission │                │
├─────────┴──────────┴───────────┴───────────────┤
│ Events/recommendation clicks → generic durable cursor ─┐ │
│ Content feedback → content_feedback durable cursor ────┴→ atomic buffer+cursor checkpoint │
│ 30-day history: click events + recommendations + saved_item_removals → paged/lazy UI │
│ dislike: exact card hides synchronously; durable topic → final history/serve/push recheck │
│ discovery may keep broad search; async semantic purge optimizes inventory, not correctness │
│ cold start fence+task admission → listener; background recovery → tick_if_buffered │
│ hot reload pause/drain/recover then rebind; periodic maintenance alone calls tick │
│ Dialogue → typed settlement worker → learning       │
│ Legacy batch only when rollback flag=false     │
│ Init barrier: profile commit → discover/evaluate/copy → ready │
│ Bilibili supply: relevance search + budgeted 1×5 pubdate recent lane → shared evaluation │
│ Evaluation: time-neutral relevance + grounded temporal evidence → eligible / review hold / expired + publication bonus │
│ Temporal shadow: bonus vs no-bonus Top10/50/100 aggregates → class/source/age audit (no serving change) │
│ Images: proxy fg + refresh prefetch → app-stable 4/3 lane → singleflight/atomic cache │
│ Soul cognition: dual pending cooldown · one anchor · worker-only settlement · winner receipt · confusion FIFO · ledger · deep gate │
│   LLM adapters · Source adapters (SourceAdapter) │
│ Module route → LLM instance chain → adapter · SourceAdapter │
│ Optional visual prewarm: covers / profile centroids / keyframes + danmaku │
│ provenance (provider/model/dim/sampling) → empty-success / retryable fail │
│ Config recovery draft (normal/degraded) → temp probe / exact /models (no write) │
│ Local migration: checksummed .obcbackup → request-id pending ↔ status/cancel → restart replace/rollback │
│ Source-family registry: alias · strategy · URL host │
│             → pool accounting · durable seen_items ledger │
│ Bangumi public API → search/ranked/date producer → shared eval │
│ V2EX public API/Feed → bounded Topic/Reply enrichment → five modes → shared eval │
│ V2EX identity ladder: verified PAT > observed browser > accepted user; mismatch pauses only account projection │
│ Temporal lifecycle: verbatim evidence + code-owned review clock → serve / temporal_review_hold / expired │
│ Evaluator prefilter stays shadow → privacy-safe decision/raw-score join → read-only gate (no auto-enforce) │
│ Named cognition views → task gate: compact only for awareness_confusions; others legacy │
│ Token diet: per-offset preference packing; weighted recent/judged/relevant/important insight≤40 → full merge │
│ Keyword planner → safe 24h cross-digest pending reconcile → deficit/generate/claim (0=hard expiry) │
│ Admitted backlog → copy watermark ∪ visible topic-slot gap → eligible-first copy (0=legacy drain-all) │
│ API projected=available+eligible copy-pending+evaluated → 3×30 workers → serial admit → UI │
│ API raw-empty → wake under-share sources now → real progress resets / duplicate-only waves back off │
│ Delight gate: formal copy/topic ready + seen_items guard → score/snapshot → UI × writes seen ledger │
│ Inventory API/OpenClaw startup hook → recover/maintain → expose LLM │
│ Reshuffle: current-card exclusion → hold/stale retirement + PoolServeSnapshot → final temporal recheck + atomic write │
│ Platform scope (PC Web tabs only): source_platform → scoped candidates, no cross-platform floor → same rank/copy/persist │
│ Platform inventory: platform-availability → same canonical servable set → total == Σ by_platform │
│ Background maintenance: isolated worker → ≤50 rows/batch; unchanged skip / 10m sweep │
│ /api/saved/* · router · Bilibili native save      │
│ Six adapters → ExtensionNativeSaveBroker → extension_native_save_jobs │
│ seven-platform source task multiplex: xhs / dy / yt / x / zhihu / reddit / linuxdo │
│ Extension-online periodic re-pull (off by default; explicit opt-in): Runtime → six bootstrap tasks (global serial) → installed extension │
│ seven-source task multiplex: xhs / dy / yt / x / zhihu / reddit / v2ex │
│ Extension-online periodic re-pull (off by default; explicit opt-in): Runtime → six bootstrap sources (global serial) → installed extension │
│ task-result → staged durable ingress → atomic bounded seen keys (5,000/source) → terminal │
│ V2EX complete favorite snapshots → two confirmed misses → durable retraction/restore outbox → account-scoped Node affinity │
│ XHS auto tasks: source/scheduler gate → SQLite pacing/breaker → no new tab while off/limited │
│ XHS search: inactive tab → MAIN response normalization → isolated replay / DOM fallback │
│ Linux.do: isolated task tab → same-origin GET → five discovery / three bootstrap paths │
│ extension_native_save_jobs -> /api/sources/<slug>/next-task -> installed extension │
│ exact OpenBiliClaw / YouTube Watch Later targets → safe task-result    │
│ trusted-local E2E exact auth → one saved-sync item → six-field callback │
│ unsupported_adapter_missing retryable · unsupported_content_type local-only │
│ Canonical ID · Local-first sync · Task poll · SQLite (events · seen ledger · pool · recs · saved/tasks · removal snapshots)│
│ Six adapters → broker → shared MV3 recovery barrier → Reddit/X/YT/XHS/DY/Zhihu executors (6/6 fixture + real-account)│
└────────────────────────────────────────────────┘

Web/API durable → rowid reply worker → app-stable dialogue lease(max active 1) → SocraticDialogue(queued) → visible CAS
delight/legacy/interest-probe/avoidance chat ───────────────────────────────┘ (reply + required effects share the lease)
post-reply 11-kind learning/settlement → independent typed settlement worker (not reply backlog)
CLI/OpenClaw → SocraticDialogue(legacy_direct) → user+agent history → direct learning outside queue/guard
learning → bypass background admission; keep total gate ── new dislike: shared purge → content_cache
transient/provider/timeout/cancel → rollback provisional history → durable pending + head retry; explicit invalid/empty → failed CAS
durable turn → fixed time/payload → confirmation entry (pending list/cards) → frozen anchor admission → relation matrix
                                                  └→ card/anchor/chat/probe/confusion/replay/legacy all worker-only
card action → synchronous 200 fast path | 202 processing → popup/mobile/desktop poll; CLI has no action

Desktop startup: recommendation hydration │ runtime hydration │ secondary health/profile/activity/config hydration (independent)
Desktop background resume (cards already loaded): skip the pool-filling recommendation GET │ sync runtime / inventory status only

Overseas traffic: `[network].mode` → system proxy (default) / direct / custom proxy → LLM, YouTube, X/Reddit CLIs, Bangumi, updater, GitHub project stats; CN clients including V2EX remain isolated and direct
Manual Douyin discovery: CLI discover → daemon-equivalent producer → per-keyword outcomes → extension search/hot/feed → pending-eval pool
```

### Optional visual and danmaku prewarming

When `[discovery].keyframe_enabled` is on and multimodal embedding is available, keyframes build the
same visual centroids used by P1; the P1 cover bonus is still controlled only by
`visual_profile_enabled`. Keyframe cache provenance includes the sampling algorithm,
`keyframe_max_frames`, embedding fingerprint, and dimension, so a model or sampling change rebuilds
safely. Partial keyframe results carry stable sampled slots: successful slots may enter cache first,
but completion is recorded only for confirmed no-data or a complete sample whose every embedding
succeeds. Failed slots remain eligible for the next cycle.

`keyframe_fetch_limit`, `danmaku_fetch_limit`, and `danmaku_max_chars` are range-validated in both the
config file and config API. Danmaku summaries use the full `danmaku_max_chars` value for document
embedding rather than a silent fixed prefix. Cross-platform visual bonuses keep zero fixed; on
multi-platform batches both signs align to the observed global side maximum under the combined cap,
while single-platform batches retain absolute magnitude. Zero / missing values stay zero. See
[`docs/modules/recommendation.md`](docs/modules/recommendation.md) and [`docs/architecture.md`](docs/architecture.md) for the full contract.

Remote extension access uses explicit, default-off device authentication: `ext-key generate` → digest-only backend config → `/api/auth/extension-token` short session. HTTP uses a Bearer header; only WebSocket and image proxy URLs carry the short session query.

Public-domain Docker deployments may add the default-off
[Caddy HTTPS overlay](docs/https-deployment.md). It obtains and renews a trusted certificate,
proxies REST and WebSockets through shared loopback, and restricts host port `8420` to loopback.
Trusted LAN and self-managed deployments can instead use the built-in TLS Proxy for exact HTTPS
Origin/Host checks and explicit local-CA SANs; with no remote SAN its generated certificate is
localhost-only. The two edges are mutually exclusive, and the default HTTP path is unchanged.

> Full architecture detail (runtime state machine, pool accounting, profile overrides, and more) lives in [Architecture](docs/architecture.md) and the [visual architecture diagrams](docs/index.md).

### Content Discovery Engine

**Multi-source adapter architecture** — every platform plugs in through the `SourceAdapter` protocol, each with its own discovery approach:

| Source | Discovery | How data is fetched |
|--------|-----------|---------------------|
| **Bilibili** | search · trending · related chain · cross-domain explore | Backend-direct WBI-signed APIs, with a real rendered search-page fallback via the extension |
| **Xiaohongshu** | passive collection · search · creator subscriptions · init import | Extension reads your logged-in pages; zero backend crawling |
| **Douyin** | init import · search · hot · feed | CLI and daemon share the formal producer; extension background tabs fetch candidates for the unified eval pool |
| **YouTube** | init import · Takeout offline import · search / trending / channel | Extension reads profile signals; steady-state refill is backend-direct |
| **X (Twitter)** | init import · search · For-You · followed authors | Server-side read-only cookie replay for discovery; native bookmark executor's first real favorite finished `synced` |
| **Zhihu** | init import · search · hot · feed · creator · related | Extension reads logged-in tabs; renders as text cards |
| **Reddit** | init import · search · hot · subreddit · related | Backend rdt-cli for discovery by default; Saved executor is fixture-tested, but the first real write remains uncertain after a 2xx response lacked old-DOM confirmation |
| **Linux.do** | bookmark / like / read-history init · search · hot · latest feed · creator · related | Extension performs same-origin read-only GETs in a real `linux.do` task tab; public discovery needs no login, and cookies/raw responses are never uploaded |
| **Bangumi** | public-collection init · search · ranked · date browse | Official anonymous read-only API; no cookie/token, and date results may include unreleased subjects |
| **V2EX** | search · Node · Tab · hot · latest | Official anonymous API / JSON Feed; optional PAT for API 2.0 enrichment; Topic text cards |
| **Generic Web** | browser + LLM extraction | Adapts to any webpage |

What happens after discovery:

- **Safe fetching** — the backend never logs in for you and never crawls content you can't see; every platform reuses the sessions already in your browser, and first-run profile signals are pulled only after you click "Start initialization." Periodic account re-pull is off by default. It runs only after explicitly setting `source_incremental_enabled=true`, while the extension is online, and does not affect manual initialization, manual fetches, or background discovery. Douyin remains separately default-off. Linux.do tasks permit GET only, and `_t` is used solely as a login boolean.
- **Continuous unified evaluation** — raw candidates share one eval pool and are scored against your Soul profile, content text, and recent negative feedback. The default 3×30 workers refill immediately, scheduling counts only durable stock, and serial admission is capped by current headroom. Optional embedding prefiltering starts in shadow mode before enforce may skip clearly low-similarity items.
- **Diversity selection** — platform quotas → topic dedup → style balancing → cross-platform interleaving → count caps; only Bilibili is enabled out of the box, other platforms are switched on in settings.

> Per-platform task pipelines, pool accounting, and fallback strategies are documented in the [Discovery Engine docs](docs/modules/discovery.md).

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
│   ├── sources/               # Source adapters, Bangumi/V2EX APIs, and XHS/Douyin/YouTube/Zhihu/Reddit/Linux.do/V2EX task bridges
│   ├── youtube/               # Google Takeout import parser
│   ├── api/                   # Local FastAPI (config rollback / degraded mode / popup API)
│   ├── tls_proxy.py           # Default-off LAN/self-managed HTTPS edge
│   ├── runtime/               # Refresh, feedback coalescing, presence gate, shared CLI/desktop autostart reconcile, Ollama, degraded RuntimeContext
│   ├── bilibili/              # Bilibili API layer (WBI signing · rate control)
│   ├── llm/                   # Multi-model LLM adapters + structured JSON tolerance
│   └── storage/               # Data storage layer
├── extension/                 # Chrome/Firefox/Safari extension (including Linux.do/V2EX/Weibo read-only task bridges)
├── extension/                 # Chrome extension (Bilibili + XHS + Douyin + YouTube + X + Zhihu + Reddit + Weibo recovery/tasks)
├── skills/                    # Built-in Skill definitions
├── docs/                      # Documentation
└── tests/                     # Tests (1900+)
```

> The native mobile client (Flutter, Android / iOS / Web / Linux / macOS / Windows) lives in the separate repo [OpenBiliClaw-mobile](https://github.com/whiteguo233/OpenBiliClaw-mobile).

## 🛠️ Tech Stack

| Module | Technology |
|--------|-----------|
| Backend | Python 3.11+ |
| Browser Extension | TypeScript + Chrome Extension (Manifest V3) |
| LLM | Multiple independent Base URL / token / model instances per provider type, with ordered global and per-module failover chains; first migration keeps a permanent legacy backup and `config-export-legacy` creates an old-version copy; built-in Gemini / DeepSeek / OpenAI / Claude / OpenRouter / OrcaRouter / Ollama; any OpenAI-compatible endpoint works; OpenAI can experimentally reuse Codex CLI ChatGPT OAuth via the official Codex transport |
| Bilibili API | Custom client (WBI signing · v_voucher auto-recovery · rate control) |
| Xiaohongshu | Extension DOM/state extraction + task dispatch; search/creator run in background tabs and search uses a MAIN-world page-response bridge when hidden virtual DOM is absent; only scrolling init opens `/explore` in the foreground and clicks the profile entry; no backend crawling |
| Douyin | Extension DOM + MAIN-world passive fetch tap + task dispatch; init imports post / favorite / like / follow signals; search / hot / feed discovery starts from the Douyin home page and uses DOM interactions to trigger loading; search/feed passively collect page responses / rendered results, and hot can use a hot-board `group_id` seed as a logged-in related fallback; no backend login crawling |
| YouTube | Extension DOM task dispatch reads watch history / subscriptions / likes; Google Takeout can import older data offline |
| X (Twitter) | Server-side cookie replay via default-installed `twitter-cli` (lazy-imported, read-only); the extension captures your engagement and syncs the x.com cookie; tweets render as text cards |
| Zhihu | Extension task dispatch reads event-smoke and selected guided-init signals plus search / hot / feed / creator / related candidates in the logged-in browser; answers / articles / questions render as text cards |
| Reddit | Default-installed rdt-cli reads search / hot / subreddit / related candidates by default; the extension syncs `reddit_session` into rdt credentials and `rdt login` is a manual fallback; extension task dispatch reads discovery when rdt is unavailable, unauthenticated, or explicitly selected, and always reads bootstrap saved / upvoted / subscribed signals in the logged-in browser; posts / comments render as text cards |
| Linux.do | Regular pages use the shared behavior adapter; isolated task tabs make same-origin GETs for search / hot / feed / creator / related and bookmarks / likes / read history, returning only normalized fields or structured errors; cookies and raw responses are not uploaded |
| Bangumi | Official anonymous read-only v0 API; search / ranked / date browsing feed the shared candidate pool, while an optional public username enables public-collection profile init; no cookie, token, or native write-back |
| V2EX | Official anonymous API / Feed; search / node / tab / hot / latest feed the shared candidate pool, with optional PAT read-only enrichment; the extension runs four read-only bootstrap scopes and sends only a boolean login heartbeat; no site writes |
| Optional HTTPS | Pinned Caddy Docker overlay with automatic certificates for public domains; Python TLS Proxy + `[tls]` extra and local CA/SAN for LAN/self-managed use; off by default and mutually exclusive |
| Storage | SQLite + Embedding vector index |
| Containerization | Docker Compose (backend) |
| Agent Framework | Lightweight custom framework |

## 📖 Documentation

- [Documentation Hub](docs/index.md) — All-in-one entry point
- [FAQ](docs/faq.md) — quick answers for install / connection / update issues
- [Project Spec](docs/spec.md) — Complete design & planning
- [Architecture](docs/architecture.md) — System architecture deep dive
- [Memory Design](docs/memory-design.md) — Multi-layer memory architecture
- [Discovery Engine](docs/modules/discovery.md) — multi-source discovery + platform mix + diversity selection
- [Soul Engine](docs/modules/soul.md) — Deep profiling + MBTI + interest speculation
- [CLI Reference](docs/modules/cli.md) · [Config Reference](docs/modules/config.md)
- [Contributing Guide](docs/contributing.md)
- [Flutter Mobile Client](https://github.com/whiteguo233/OpenBiliClaw-mobile) — native app in a separate repo (Android / iOS / Web / desktop)

## 📜 Release History

The current release is summarized in [Recent Updates](#recent-updates) above; full history lives in [docs/changelog.md](docs/changelog.md). Most users should use the `openbiliclaw-v*` aggregate [Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) for extension packages and available desktop installers; automation-channel releases remain available as `backend-v*`, `extension-v*`, and `desktop-v*`.

## 🗺️ Roadmap

OpenBiliClaw aims to be your **personalized entry point to the entire web**. Started on Bilibili, it now covers Xiaohongshu, Douyin, YouTube, X, Zhihu, Reddit, Linux.do, Bangumi, V2EX, Weibo, and the generic Web; next:

- **More content sources** — Weibo and other BBS / forums; each platform is a `SourceAdapter` and the architecture is proven extensible
- **Cross-platform interest fusion** — your mechanical-keyboard interest from Bilibili + your coffee-gear interest from Xiaohongshu + your short-video taste from Douyin likes/favorites + your long-form watching and subscriptions from YouTube + the news you like/bookmark on X = one complete you. Profile fusion stops your interests from being fragmented across silos
- **Smarter cross-source discovery** — "you started following coffee gear on Xiaohongshu, here's a hand-drip documentary on Bilibili you might love"
- **Community ecosystem** — user-defined SourceAdapters, shared discovery strategies, contributed platform adapters

## 🤝 Contributing

Contributions welcome! See the [Contributing Guide](docs/contributing.md) to get started.

## 🙏 Acknowledgements

- Thanks to [@addtion99](https://github.com/addtion99) for proposing configurable browser-extension backend host / port settings and sharing the popup-side implementation idea in [#8](https://github.com/whiteguo233/OpenBiliClaw/pull/8).
- Thanks to [@jiaobenhaimo](https://github.com/jiaobenhaimo) for contributing Safari extension, watch-later bookmarks, YouTube repost detection, and marketing filter designs in [#53](https://github.com/whiteguo233/OpenBiliClaw/pull/53). The OR-join dedup fix and watch-later feature have been merged into main.
- Thanks to [@tangle111-design](https://github.com/tangle111-design) for exploring `style_key` viewing modes, recommendation tone, Bilibili initialization, and LLM / profile workflow improvements in [#69](https://github.com/whiteguo233/OpenBiliClaw/pull/69). The relevant ideas have been reviewed, split up, and selectively merged into main.
- Thanks to [@DongLanQwQ0](https://github.com/DongLanQwQ0) for polishing desktop web interactions — side-drawer collapse animation, a delight-card drag dead zone, and a stacked toast notification system — in [#102](https://github.com/whiteguo233/OpenBiliClaw/pull/102). Merged into main.
- Thanks to [@DongLanQwQ0](https://github.com/DongLanQwQ0) for the desktop web theme-engine rework to oklch in [#110](https://github.com/whiteguo233/OpenBiliClaw/pull/110) — a single `--hue-primary` control point with a 12-hue tunable color picker, a five-step accent ramp, and unified interaction states. Merged into main.
- Thanks to [@wuwafly3](https://github.com/wuwafly3) for continued work on multimodal recommendations: [#100](https://github.com/whiteguo233/OpenBiliClaw/pull/100) introduced the DashScope (Alibaba Model Studio) multimodal embedding provider and image-only cover vectors, while [#135](https://github.com/whiteguo233/OpenBiliClaw/pull/135) added the user visual profile (P1), Bilibili danmaku semantics (P2), video keyframes (P3), and cross-platform visual weighting pipeline. Mainline follow-up hardened the contracts and retry behavior, added configuration surfaces, and completed real-environment validation.
- Thanks to [@LHMQ878](https://github.com/LHMQ878) for fixing the `agent_bootstrap` TOML instance-section matching in [#182](https://github.com/whiteguo233/OpenBiliClaw/pull/182): quoted section headers such as `[llm.instances."openai"]` are now treated as the same table as bare keys, preventing duplicate table declarations and `tomllib` failures when bootstrap is run again. Merged into main.

## ⭐ Star History

If OpenBiliClaw gave you back control of your feed, [a star](https://github.com/whiteguo233/OpenBiliClaw) is the most direct vote for "keep adding platforms".

<a href="https://github.com/whiteguo233/OpenBiliClaw">
  <img alt="GitHub Stars" src="https://img.shields.io/github/stars/whiteguo233/OpenBiliClaw?style=flat-square&logo=github&label=Stars" />
</a>

> ⚠️ The live Star History chart is temporarily replaced by a star badge: since 2026-06-30 GitHub restricts the stargazers API to a repo's admins and collaborators, so star-history charts cannot render for now. We'll restore the live chart once upstream recovers or a new encrypted token is configured.

## Privacy at a glance

Default data flow: browser extension → your configured local OpenBiliClaw backend → SQLite on your machine. The extension does not send data to servers operated by OpenBiliClaw developers. Linux.do `_t` is reduced to a browser-local login boolean; cookie values, CSRF data, and raw site responses are not uploaded. If you configure a cloud LLM or embedding provider, the relevant content is sent to that provider according to your configuration. See the [Privacy Policy](docs/privacy.md).
Default data flow: browser extension → your configured local OpenBiliClaw backend → SQLite / data files on your machine. The extension does not send data to servers operated by OpenBiliClaw developers. If you configure a cloud LLM or embedding provider, the relevant content is sent to that provider according to your configuration. A `.obcbackup` you explicitly export from Settings may contain model/source API keys, cookies, your profile, and history, and it is **not encrypted**. It excludes the source machine's entire API-auth section (including passwords, sessions, and device keys), but must still be transferred only between trusted devices. See the [Privacy Policy](docs/privacy.md).

## 📄 License

[MIT](LICENSE)

## Friend Links

<details>
<summary>Friend Links</summary>

[![LINUX DO](https://img.shields.io/badge/LINUX_DO-Friend%20Links-4D6BFE?style=flat-square&logo=discourse&logoColor=white)](https://linux.do/)

</details>
