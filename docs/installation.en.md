# Installation and deployment

[Back to README](../README_EN.md#quick-start) · [中文](installation.md) · [FAQ](faq.md)

For a first installation, follow **desktop backend → browser extension → models and sources → initialization**. This guide also covers AI agents, scripts, Docker, and source installs.

## Choose a route

| Your setup | Route |
|---|---|
| macOS / Windows, trying the product | [Desktop installer](#desktop) (experimental pre-release) |
| An AI agent will configure your environment or edit the source | [AI one-line deployment](#ai-deploy) |
| Linux / WSL2 or a terminal workflow | [Installer script](#script-install) |
| An existing Docker environment | [Docker](#docker) |
| Development or source debugging | [Manual installation](#manual) |

## Before you start

- **A computer that keeps the backend running.** Phones and extensions connect to it; they do not each run another recommendation backend.
- **A working LLM service** for profiles, recommendation explanations, and chat. Prepare its API key / endpoint / model; usage is billed by your chosen provider.
- **An independent embedding service** for content vectors and similarity. Local Ollama `bge-m3` is the default recommendation, with remote services available by explicit configuration. It does not automatically inherit your chat provider.
- **At least one source that returns profile signals**, with a signed-in browser or public username as required by that source.

The larger desktop installer only bundles the embedding model. An entirely local setup also needs a chat LLM and suitable hardware; see the [local LLM hardware requirements](agent-install.md).

<a id="desktop"></a>

## 1. Install the desktop backend

Download the installer for your OS from [Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest):

- **macOS**: Apple silicon uses `OpenBiliClaw-macos-v*-arm64.dmg`; Intel uses `OpenBiliClaw-macos-v*-x64.dmg` when the release provides it. Open the DMG and double-click `安装并启动 Install OpenBiliClaw.command` to install and launch.
- **Windows**: download `OpenBiliClaw-windows-*-Setup.exe`, double-click to install, keep “Launch OpenBiliClaw” selected, and click Finish.

**Package options.** Both variants bundle the Ollama runtime. The **lean** installer downloads `bge-m3` on first launch; the **`-with-embedding`** installer includes its ~1.1GB embedding model. **A working LLM must still be configured separately**: bundled embeddings do not include a chat model or make the entire app offline.

The app lives in the **macOS menu bar / Windows system tray**; right-click for "Open Web UI / View runtime logs / Quit".

> ⚠️ **First launch: macOS security blocking / Windows SmartScreen**:
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
> This is an **experimental pre-release**: unsigned, rolling with the backend version, best for trying it fast without the command line. To edit the source, use [AI one-line deployment](#ai-deploy) below.

<a id="mirrors"></a>

> 🇨🇳 For mainland China downloads, use [123 Cloud (v0.3.221 archive)](https://4001474255.share.123pan.cn/123pan/IxbZMh-90KO3); the share holds 7 packages from v0.3.221, not necessarily the current release. Compare versions with GitHub before downloading. The extension, regular Windows installer, and source are also available from GitHub / the [Gitee v0.3.221 release](https://gitee.com/whiteguo233/openbiliclaw/releases/tag/openbiliclaw-v0.3.221).

<details>
<summary>Installer upgrade behavior and Windows silent installation</summary>

- **macOS**: download the DMG that matches your Mac: `OpenBiliClaw-macos-v*-arm64.dmg` for Apple silicon, or `OpenBiliClaw-macos-v*-x64.dmg` for Intel when the release provides it. The recommended path is to double-click `安装并启动 Install OpenBiliClaw.command`: it verifies the new bundle, quits the old instance, atomically replaces the app in Applications, and launches the version just installed. Traditional drag-and-drop remains available, but upgrades must quit the old version first and reopen the replacement manually.
- **Windows**: download `OpenBiliClaw-windows-*-Setup.exe` — double-click to install. The final wizard page offers a checked "Launch OpenBiliClaw" checkbox; the newly installed version starts only when you click Finish (upgrades stop mutex-less old instances first; when the running app is an AppMutex build, Setup/Uninstall first show the standard "OpenBiliClaw is currently running" prompt and re-check after you close it — clicking Finish hands off to the new version, and unchecking skips the launch). `/SILENT` / `/VERYSILENT` installs have no wizard pages and still launch the new version automatically once Setup succeeds; with the app still running, a silent install/uninstall paired with `/SUPPRESSMSGBOXES` is cancelled instead, so close the app first.

</details>

<details>
<summary>Bundled dependencies, optional Tailnet, and macOS compatibility</summary>

Both variants include the default source dependencies including X's `twitter-cli` and Reddit's `rdt-cli`, and the default-off embedded Tailnet helper. The latter lets the desktop app join your tailnet without installing system Tailscale. Enable it from Desktop Web or the browser extension's **Settings → General**, choose browser login, an Auth Key, or an OAuth Client Secret plus an authorized device tag, then fully restart the app. Reddit's rdt command backend prefers the connected extension's synced `reddit_session`; `rdt login` remains a manual fallback, and unauthenticated runs fall back to extension tasks.

The macOS app still targets 10.15+. Only the optional Tailnet helper built with Go 1.26.6 has a
measured minimum of macOS 12; on 10.15 / 11 the local app continues normally while this remote edge
is unavailable.

</details>

<details>
<summary>Data locations, migration from older installers, and configuration recovery</summary>

Data uses the same directory as the AI / script installers: `~/OpenBiliClaw` (macOS / Linux) / `%USERPROFILE%\OpenBiliClaw` (Windows), and survives upgrades and uninstalls. Data from older packaged builds under `~/Library/Application Support/OpenBiliClaw` / `%LOCALAPPDATA%\OpenBiliClaw` is copied back on first launch without overwriting existing files. If a broken `config.toml` / `config.local.toml` prevents startup, the desktop package backs the bad file up as `*.invalid`, regenerates the default config, then opens `/setup/` so initialization can run again; `data/` is left untouched.

</details>

<details>
<summary>Release aggregation and channel rules</summary>

The `openbiliclaw-v*` aggregate Latest Release page shows:

- Current backend source tag: `backend-v*`
- Current extension release: `extension-v*`, with `openbiliclaw-extension-v*.zip` / `openbiliclaw-extension-v*-firefox.zip` (Firefox temporary debugging); AMO signing-enabled releases also include `openbiliclaw-extension-v*-firefox.xpi` (regular Firefox install)
- Current desktop installer release: `desktop-v*`, with available `.dmg` / `.exe` assets when the same-version desktop channel has shipped; missing channels are shown as unpublished instead of being backfilled from a previous release

</details>

## 2. Install the browser extension

The extension connects browser sessions and provides in-page interaction. It shows the sidebar on supported sites, records feedback, and runs bounded read-only tasks for sources including Zhihu, Reddit, Linux.do, V2EX, and Weibo. Linux.do, V2EX, and Weibo task tabs are isolated from passive behavior collection; Weibo public discovery still runs independently in the backend.

Built on Manifest V3, the extension works in any Chrome-compatible browser — **Chrome, Edge, Brave, Arc, Vivaldi, Opera**, and more; a **Safari (macOS)** build is also provided. Releases automatically attach `openbiliclaw-extension-v*-safari.dmg` (Developer ID-signed and notarized when Apple credentials are configured, otherwise an ad-hoc experimental build that requires Safari's "Allow Unsigned Extensions"), and you can also convert the local build to an Xcode project via Apple's `safari-web-extension-converter` (see the [Safari build guide](safari-extension-build.md)).

**Convenient · one-click from the Chrome Web Store** (the browser keeps it auto-updated — best if you don't want to update manually; downside: the version can lag behind Releases):

> 👉 **[Install OpenBiliClaw on the Chrome Web Store](https://chromewebstore.google.com/detail/cdfjfkdjjhdaccbldipkjhpibnfbiamg)** — click "Add to Chrome".

**Manual install · download the latest available build from the Latest Release aggregate page** (gets the newest features and fixes — the Chrome Web Store listing usually lags by a few days to a couple of weeks due to review scheduling):

1. Open [OpenBiliClaw Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest), the newest user-facing aggregate `openbiliclaw-v*` release
2. Chrome / Edge / Brave users download `openbiliclaw-extension-v*.zip`; Firefox users install `openbiliclaw-extension-v*-firefox.xpi` when it is present, otherwise download `openbiliclaw-extension-v*-firefox.zip` and load it temporarily through `about:debugging`; Safari (macOS) users download `openbiliclaw-extension-v*-safari.dmg`, launch the app once, then enable OpenBiliClaw in Safari Settings → Extensions
3. Open the extensions page (Chrome: `chrome://extensions/` · Edge: `edge://extensions/` · Brave: `brave://extensions/`), enable "Developer mode" in the top right
4. Chrome / Edge / Brave users drag the downloaded `.zip` file into the page to install; Firefox `.xpi` files install directly, while the temporary zip must be unzipped before loading `manifest.json`

Extension updates depend on the install channel: Chrome Web Store / Edge Add-ons and the Firefox AMO listed build after approval are updated by the browser; GitHub Release Chrome zips / Firefox signed XPIs / Firefox temporary zips / Safari DMGs, developer-mode loads, and Firefox temporary installs must download the new package and reload it manually. Firefox AMO listed review is asynchronous; until the listed version is publicly approved, prefer a signed `*-firefox.xpi` from Releases when available, and use the `*-firefox.zip` temporary package only when no XPI is provided. After approval, Firefox will update the listed install natively. The backend "auto update" switch only updates the local backend source checkout, not the browser extension.

<a id="firefox"></a>

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

## 3. Configure models and content sources

Open `http://127.0.0.1:8420/setup/`, choose an LLM service, enter its credentials and model, and check the independent embedding service. The lean desktop installer needs network access for its first model download; the larger installer skips that download but still needs an LLM configuration.

By default, log in to [Bilibili](https://www.bilibili.com) and keep Bilibili selected to build the first profile and recommendations. Otherwise select another signed-in source such as Xiaohongshu, Douyin, YouTube, X, Zhihu, Reddit, [Linux.do](https://linux.do), or [V2EX](https://www.v2ex.com), or choose Bangumi / GitHub with a public username. Keep at least one source that can return profile signals. Bangumi / GitHub without identity still support public discovery but cannot initialize a profile alone; a GitHub PAT is optional.

Select only the sources you want to use for initialization. Bilibili is enabled by default; other sources are explicit choices in the setup wizard or settings. Public discovery and personal profile initialization have different requirements; see [source access and limitations](#source-access).

## 4. Initialize and view your first recommendations

Check model connectivity and source status in the setup wizard, then start initialization. The backend reads the selected signals, builds your profile, and starts discovery. Timing depends on your models, network, and source data. Once ready, open `/web` to read recommendations and explanations, then give like / not-interested or chat feedback to guide later recommendations.

If a model check fails, fix that service's key / endpoint / model or embedding connection before continuing. AI and scripted installs run init automatically after service checks pass.

<a id="mobile"></a>

## Desktop, mobile, and other clients

The backend serves desktop and mobile Web interfaces. They call your configured backend API; browser sessions and cookie sync still belong to the extension. Open the desktop package from the menu bar / tray, or run `openbiliclaw start` for a source install.

- **Desktop**: open `http://127.0.0.1:8420/web` (or `http://127.0.0.1:8420/`, auto-redirects). Two-column editorial layout with recommendations, 30-day history, profile, chat, messages, and settings all on one page.
- **Mobile**: click the phone icon in the extension header to scan the QR code, or type `http://<your-LAN-IP>:8420/m/` manually. Best for browsing recommendations, revisiting 30-day history, profile, and chat on your phone.
- **Native Flutter client**: download the Android APK (`arm64-v8a` for modern devices, `armeabi-v7a` for older ones) or the unsigned iOS IPA (re-sign with your own Apple account) from the [Latest Release](https://github.com/whiteguo233/OpenBiliClaw-mobile/releases/latest). Use the computer's LAN IP nearby; away from the LAN, the native Android / iOS app's embedded tsnet can pair with the computer's default-off embedded Tailnet path. Join both nodes to the same tailnet, use its MagicDNS name / Tailnet IP, and enable the app password. This does not cover the repo's Web, Linux, macOS, or Windows Flutter builds.

> During `openbiliclaw init`, you'll be asked whether to allow LAN access (default Y). If you chose N or want to change it later, edit `[api].host` in `config.toml` (`0.0.0.0` = LAN-reachable over available IPv4 and IPv6, `127.0.0.1` = local only). QR links prefer IPv4 and automatically use a bracketed IPv6 literal when IPv4 is unavailable.

After opening `/m/`, save it as a home-screen shortcut: on iPhone / iPad, use Safari's Share menu and choose "Add to Home Screen"; on Android Chrome / Chromium browsers, use the menu item "Install app" or "Add to Home screen". LAN HTTP may only create a shortcut in some Android browsers; full PWA install prompts are more reliable behind HTTPS in a trusted local setup.

The bottom bar now has four top-level tabs: Recommendations, Content Library, Profile, and Chat. Content Library contains Watch Later, Favorites, and History as child tabs. History pages through the last 30 days as opened, surfaced-but-unopened, and recently removed content; multiple removal contexts stay on one card, and Favorite and Watch Later can be restored independently. Old direct links to the three former tabs migrate to the matching Content Library child.

## Mobile access away from your LAN: embedded Tailnet

Away from your LAN,the **native Android / iOS apps** from `OpenBiliClaw-mobile` embed `tsnet`, and
the computer can let OpenBiliClaw itself join the same tailnet. Its Web, Linux, macOS, and Windows
Flutter builds are outside this feature's support scope. Desktop installers bundle the helper. On the
computer, open either Desktop Web or the browser extension's **Settings → General → Embedded
Tailnet access**. Enable it, then either leave the credential blank for browser login, enter a
`tskey-auth-…` Auth Key, or enter a `tskey-client-…` OAuth Client Secret plus one of that client's
authorized device tags; fully restart the app afterward. The credential is staged privately for the
next start and is never written to `config.toml`, echoed by the API, or logged. Source / scripted installs first run `openbiliclaw tailnet build-helper` with Go
1.26.6, then `openbiliclaw tailnet enable` and restart. The computer needs no system-wide Tailscale;
this default-off edge is tailnet-private and enables no Funnel/Serve. Enable the app password in the
local Web settings as defense in depth (source installs may also use `openbiliclaw set-password`). See
[Embedded Tailnet](modules/tailnet.md).

## Remote browser connections and HTTPS

Chrome Web Store / AMO builds only declare local-backend permissions by default. When you select a protocol and enter another LAN or remote endpoint, the browser requests `scheme://host/*`; WebExtension host permissions cannot be port-scoped across browsers, while actual requests remain pinned to the configured port. Public hosts require HTTPS. Enable the default-off device flow first with `ext-key generate` and `ext-key enable`.

With a public DNS name, the shortest path is the [`docker-compose.https.yml`](../docker-compose.https.yml) overlay: Caddy obtains and renews the certificate automatically, and desktop, mobile, and the extension share `https://<domain>`. Commands and required access controls are in the [HTTPS deployment guide](https-deployment.md).

## Other deployment routes

<a id="ai-deploy"></a>
<a id="ai-install"></a>

### AI one-line deployment

Paste this whole prompt into Claude Code, Codex CLI, Cursor, Windsurf, or another AI coding agent. The parenthetical note is for the agent; you do not need to understand it.

```text
Please follow https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md to deploy the OpenBiliClaw backend for me (use Bash `curl` to fetch the document, NOT WebFetch — WebFetch summarises markdown and drops critical commands).
```

The agent will clone the repo, install dependencies, start the backend with the LAN-accessible default bind (`0.0.0.0:8420`), run a health check, and ask a few questions with defaults. Before auto-init, it verifies that the ordered global LLM instance chain and the independent embedding service answer real lightweight calls; if either fails, init is blocked until you fix the service. If unsure, pick the default. Xiaohongshu, Douyin, YouTube, X, Zhihu, Reddit, Linux.do, Bangumi, V2EX, Weibo, and GitHub signals are used in the initial profile only when you explicitly opt in. Bangumi and GitHub public discovery need no login; public collections or starred repositories seed the profile only after a public identity is resolved. Weibo public discovery is also anonymous, while personal initialization requires a signed-in Weibo browser and extension.

Source installs do not compile the Tailnet helper by default. Only when the user explicitly wants
native Android / iOS app access away from the LAN, install Go 1.26.6 and run
`openbiliclaw tailnet build-helper`, then `openbiliclaw tailnet enable`, and fully restart. The first
Docker image does not bundle this helper.

<a id="script-install"></a>

### Installer scripts

macOS / Linux / WSL2 (Bash):

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

Native Windows (PowerShell, no Docker or WSL2 required):

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

The script needs `git` and Python 3.11+. It clones the repo, then asks for the preferred LLM instance, embedding, Bilibili cookie, and Xiaohongshu / Douyin / YouTube opt-ins before installing dependencies or starting the backend. Once confirmed, it starts the backend, verifies the global LLM instance chain and embedding service, then runs init to build the first profile and discovery pool. X, Zhihu, Reddit, Linux.do, Bangumi, V2EX, Weibo, and GitHub can be enabled afterward in `/setup/` or settings. Public Linux.do, Bangumi, V2EX, Weibo, and GitHub discovery needs no login; Weibo personal initialization needs a signed-in Weibo browser and extension, while Bangumi / GitHub initialization can use a public username and GitHub's PAT remains optional. If unsure, press Enter or choose the default.

<a id="docker"></a>

### Docker deployment

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

Source builds, upgrades, and troubleshooting: [Docker Deployment Guide](docker-deployment.md).

<a id="manual"></a>
<a id="manual-install"></a>

### Manual installation and debugging

> Human reference: [docs/agent-install.md](agent-install.md) (short agent-facing contract) and [docs/agent-deployment.md](agent-deployment.md) (long-form troubleshooting).

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

The commands below assume `.venv` is activated. After `uv sync`, you can prefix each command with `uv run` instead.

```bash
# If using local embeddings, prepare the independent service first
openbiliclaw setup-embedding

# After LLM and embedding checks pass, fetch history and build the first profile
openbiliclaw init

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

<a id="source-access"></a>

## Source access and limitations

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
| **Weibo** | No login for public discovery; sign in at https://weibo.com in the extension browser for personal initialization | Public discovery still works signed out, but personal initialization signals are unavailable |
| **GitHub** | No login required; optionally enter a public username for public starred repositories. A PAT only improves rate limits and verifies identity through `/user` | Anonymous search/ranked/latest discovery still works without identity, but GitHub cannot initialize a profile alone; browser cookies are never read |

Xiaohongshu, Douyin, YouTube, Zhihu, and Linux.do use Chrome extension tasks; Reddit defaults to backend-installed rdt-cli for steady-state discovery and keeps the extension for init signals; X discovery uses server-side cookie replay. GitHub always uses the backend official REST client for public repositories and never enters an extension task. Linux.do requests are same-origin GETs inside real site tabs; `_t` is reduced to a login boolean and neither cookie values nor raw responses are uploaded. Reddit/X, YouTube, Xiaohongshu, Douyin, and Zhihu native-save executors are wired 6/6 and fixture-tested; in the 2026-07-14 real-account regression, every platform's favorite and watch-later/favorite-fallback path finished `synced/already_synced`. YouTube uses named playlists; current Zhihu exposes a global `收藏 / 已收藏` toggle with the target label `知乎收藏`, and both an initially saved state and fresh-document verification are strictly read-only so the extension never blindly clicks Save again. Linux.do and GitHub expose no native write-back. `[sources.browser].cdp_url` remains available only for generic Web / custom webpage fetching.

## Local embedding / Ollama

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

## Troubleshooting

Start with the [FAQ](faq.md), then consult [deployment troubleshooting](agent-deployment.md), or the [Docker guide](docker-deployment.md). See the [configuration reference](modules/config.md) and [CLI reference](modules/cli.md) for full details.
