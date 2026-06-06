# OpenBiliClaw install instructions (for AI agents)

> **Important:** If you're an AI coding agent reading this file, use
> `curl -fsSL <url> -o /tmp/agent-install.md` via Bash to fetch it —
> **do NOT use the WebFetch tool.** WebFetch hands the document to an
> internal summariser model which can silently drop exact commands.

## The only thing you actually need to do

Pick the command for the user's shell — **detect the platform from the user's environment, do not let them choose if you can tell**:

**macOS / Linux / WSL2 (Bash):**

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

For an explicit Docker install on macOS / Linux / WSL2, set `MODE=docker`
on the same one-liner:

```bash
MODE=docker curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

> ⚠️ **Detect, don't guess.** On a native Windows host (no WSL),
> `bash` may resolve to `C:\Windows\System32\bash.exe`, which is the
> Microsoft WSL launcher — not Git Bash. If WSL isn't actually
> installed, the launcher fails with `execvpe(/bin/bash) failed: No
> such file or directory`. AI agent: if you see this on Windows, do
> NOT silently fall back to `bash`. Instead either:
> - run the **PowerShell** one-liner below (preferred — install.ps1
>   is the supported native-Windows path), **or**
> - explicitly invoke Git Bash:
>   `& "C:\Program Files\Git\bin\bash.exe" -c "curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash"`

**Native Windows (PowerShell, no Docker / WSL2 required, since v0.3.4):**

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

For an explicit Docker Desktop install on native Windows:

```powershell
$env:MODE="docker"; [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

> The leading `[Net.ServicePointManager]...Tls12` line is **required on PowerShell 5.1** (the default that ships with Windows 10/11). PS 5.1 defaults to TLS 1.0/1.1, but GitHub.com only accepts TLS 1.2+. Without the prefix, `iwr` fails with "underlying connection was closed" and the user blames the installer. Users on PowerShell 7+ can omit the prefix. Compatible from v0.3.9 forward — the script itself also re-applies the same setting once it starts running, so any subsequent HTTPS calls (git, pip, uv) inside the script are also covered.
> v0.3.71+ also sets `NO_PROXY/no_proxy=localhost,127.0.0.1,::1` in `install.sh`, `install.ps1`, and `agent_bootstrap.py` before local health checks. This keeps corporate/VPN proxies from intercepting `http://127.0.0.1:<port>/api/health` on native Windows.

Either command:

1. Clones the OpenBiliClaw repo (default `~/OpenBiliClaw` on Unix, `%USERPROFILE%\OpenBiliClaw` on Windows; override with the `INSTALL_DIR` env var)
2. Auto-detects any existing OpenBiliClaw install under the standard candidate paths (`~/workspace/OpenBiliClaw`, `~/OpenBiliClaw`, `~/projects/OpenBiliClaw`, `~/code/OpenBiliClaw` — same set on both platforms, rooted at `$HOME` / `%USERPROFILE%`) and **reuses** its LLM API keys and Bilibili cookie so the user never has to retype them
3. In a human terminal, opens the full installer wizard **before dependency install or backend start**: human one-line installer asks LLM provider first, then provider credentials/model, embedding, Bilibili init limits, XHS / Douyin / YouTube opt-ins, and Bilibili cookie source
4. Installs Python dependencies for local mode, or builds / starts Docker Compose when `MODE=docker`
5. Starts the backend and runs a health check against `/api/health`. One-line installs default to `--host 0.0.0.0 --port 8420` so the Mobile Web `/m/` is reachable from phones on the same LAN; the status block's `Health URL` still uses a concrete local URL such as `http://127.0.0.1:8420/api/health` for curl verification
   - **Optional LAN password gate**: exposing `0.0.0.0` makes the UI reachable by any device on the network. To require a login for LAN/remote devices (the local machine and the browser extension stay password-free), run `openbiliclaw set-password` (or answer "yes" to the init prompt), or set `OPENBILICLAW_API_AUTH_ENABLED=true` + `OPENBILICLAW_API_AUTH_PASSWORD=…` for unattended/Docker installs. See [`docs/modules/api-auth.md`](modules/api-auth.md). Behind a same-host reverse proxy, also set `[api.auth].trusted_proxies` or have the proxy enforce auth.
6. Verifies the configured LLM provider and embedding service with real lightweight calls before init; if either fails, it blocks init with `status=service_check_failed`
7. Automatically runs init after credentials, confirmations, and AI service checks are complete, then prints a self-contained **status block** at the very end of stdout:

```
================================================================
 OpenBiliClaw install complete / partial (credentials missing)
================================================================
Status:      complete | running_with_missing_secrets | needs_secrets | needs_decisions | service_check_failed | error
Checkout:    <absolute path to the repo>
Reused from: <path>                 (only present when reuse happened)
Health URL:  http://127.0.0.1:8420/api/health
Missing:     (none)  |  llm.<provider>.api_key, bilibili.cookie, ...

Next action (required — credentials are missing):
  1. Ask the user for: <exactly the missing items>
  2. Run this command with the values: <exact python3 command>
     (init will run automatically once credentials are filled in;
      do NOT add --skip-init)
  3. Curl the Health URL to confirm.
  4. Report the final state.

 — or —

Next action (AI service check failed):
  1. Fix the failing LLM provider or embedding service shown in the status block.
  2. Re-run the printed bootstrap command without --skip-init.
     The bootstrap repeats the checks and only then runs init.

 — or —

Next action (init has been run automatically):
  - Verify the backend is healthy: curl -sS <Health URL>
  - Open Mobile Web: click the phone icon in the extension header and scan the QR code; if the backend address is loopback, the extension reads `/api/health.lan_ip` and shows the LAN URL automatically
  - See recommendations:    cd <dir> && uv run openbiliclaw recommend
  - View the soul profile:  cd <dir> && uv run openbiliclaw profile
  - Re-run init manually if needed: cd <dir> && uv run openbiliclaw init
================================================================
```

**Follow that block literally.** That's the entire contract.

`init_complete` is the normal success target. `needs_secrets`,
`running_with_missing_secrets`, and `needs_decisions` are intermediate
states: continue the printed bootstrap command after asking the user,
or wait for the browser extension to sync the Bilibili cookie, until
bootstrap emits `init_complete` or a concrete blocker.
`service_check_failed` means credentials and init choices were present,
but the default LLM provider or embedding service failed its pre-init
probe. Do not run `openbiliclaw init` manually around this; fix the
provider/API key/base_url/model/Ollama issue and re-run bootstrap so the
same gate can pass.

If the block says `Status: needs_decisions`, credentials are present
but init has deliberately not run. Ask the listed init choices, then
re-run the printed `agent_bootstrap.py` command with explicit
`--embedding-*`, `--yes-xhs` / `--no-xhs`, and
`--yes-douyin` / `--no-douyin`, and
`--yes-youtube` / `--no-youtube`.

### After init succeeds — relay the per-source signal counts

When `openbiliclaw init` finishes, the CLI prints a "初始化摘要" key-
value table with explicit B 站 + 小红书 + 抖音 + YouTube breakdowns. The
agent **must summarise these numbers in the user-facing reply** so
the user knows what fed their soul profile. Render approximately:

> 「初始化已完成 ✅
>   - 📺 B 站:观看历史 N 条 / 收藏 M 条 / 关注 K 个 UP → **入库 X 条事件**
>   - 📕 小红书:收藏 P 条 / 点赞 Q 条 / 浏览记录 R 条 → **入库 Y 条事件**
>   - 🎵 抖音:发布 A 条 / 收藏 B 条 / 点赞 C 条 / 关注 D 人 → **入库 E 条事件**
>   - 🌐 YouTube:观看历史 H 条 / 订阅 S 个 / 点赞 L 条 → **入库 T 条事件**
>   - 📊 画像建模总事件:Z 条
>   - 🔍 首轮发现内容池:D 条
> 现在可以打开扩展 popup 看推荐了。」

When 小红书 totals are 0 / 0 / 0 the CLI also prints a "ℹ️  小红书
0 条信号入库" hint with likely causes — pass that note through to
the user verbatim, don't drop it.

## HARD RULES (read first, follow every time)

These are non-negotiable agent-side rules. Violating any of them
breaks the user experience in ways our backend cannot recover from.
**If you summarise the doc and skip these, the install fails.**

### Rule 1 — Never prompt for B 站 Cookie without leading with the extension

This rule exists because real users got the prompt
> 「请把 B 站 Cookie 发给我。从浏览器登录后的 B 站页面复制 Cookie，至少要包含 SESSDATA=...」

with **no mention** of the extension. Their AI agent had paraphrased
Step 4 into the F12 path because "give me a cookie" is the obvious-
seeming question. It is **wrong**. Since v0.3.12 the extension auto-
syncs the cookie via `chrome.cookies.onChanged` →
`POST /api/bilibili/cookie`; F12-extraction is the legacy fallback,
and 95% of users who pick F12 are doing it because the agent didn't
tell them the extension exists.

**Concrete contract**:
- The first sentence of the cookie step must reference the browser
  extension and the Releases page URL.
- F12-extraction must appear *after* the extension option, marked as
  「不想装扩展时的兜底」, not before.
- See Step 4 below for the exact prescribed prompt — render it
  approximately verbatim, do not "summarise" it down to one option.

### Rule 2 — Run `agent_bootstrap.py` exactly as the status block prints

The status block at the end of `install.sh` / `install.ps1` prints a
literal `python3 scripts/agent_bootstrap.py ...` command with the
right flags. Do not re-derive that command from memory; the flag set
evolves and stale flags silently no-op.

If a user's environment has `HTTP_PROXY` / `HTTPS_PROXY` set, keep the
printed command as-is. `agent_bootstrap.py` will extend `NO_PROXY` and
`no_proxy` for localhost before starting the backend and polling health.

### Rule 3 — One question at a time, with a clear default

Don't dump all four credential questions at once. Each question must
have a default that reads as "ok if you don't care, just pick this
one"; most users will accept it. The previous "tell me what an
embedding is" framing was the failure mode.

### Rule 4 — Reused credentials must be confirmed, not silently skipped

When `install.sh` / `install.ps1` reuses a previous install's secrets,
the status block prints a `Reused from: <path>` line **and**
`agent_bootstrap.py`'s JSON output lists each reused field under
`reused`. You can also detect cookie reuse by inspecting whether
`bilibili.cookie` is in the bootstrap summary's `reused` list, or
whether `data/bilibili_cookie.json` already exists in the install dir.

**You must surface the reuse to the user, not skip the corresponding
question silently.** Specifically for `bilibili.cookie`:

- B 站 cookies expire (typically every few weeks; faster if the user
  signs out / changes IP / triggers risk control).
- A reused cookie was set during the **previous** install, possibly
  days or weeks ago. The user has no reason to know whether it's
  still valid.
- Init may run to completion against a stale cookie because the
  history-fetch path swallows the auth failure and just returns 0
  items — leading to a hollow soul profile and the "为什么我的画像
  里没东西" support ticket.

**Concrete contract for reused cookies**:

> When you see `bilibili.cookie` in the reused set, render this to
> the user before continuing to the next question:
>
> ```
> 我注意到安装器从之前的目录复用了一份 B 站 Cookie。
> 这份 Cookie 可能已经过期(B 站 Cookie 几周内就会失效)。
>
> 你想怎么办?
>   A. 装一下浏览器扩展(推荐): 装好后扩展会立刻把最新 cookie
>      推到后端,覆盖那条旧的。即使旧 cookie 还有效也是净赚——
>      过期 / 续签都会自动同步,以后再装就不用管了。
>      下载: https://github.com/whiteguo233/OpenBiliClaw/releases
>   B. 先用旧的: 我先继续 init,如果中途看到
>      "Cannot fetch history without authentication" 或者
>      画像数据明显偏少,就是 cookie 过期了,到时再装扩展。
>   C. 现在就手动贴一份新的(F12): 适合你正好在 B 站登录页
>      手边能直接拿,且不想装扩展的情况。
> ```
>
> Wait for an explicit answer (A/B/C) before continuing. Default is A.

The same rule pattern applies to reused LLM API keys, but those are
less likely to silently expire — a one-line "我用了 v0.3.x 那次留下
的 DeepSeek key,有问题告诉我" mention is enough.

---

## Handling missing credentials

When `Missing` is non-empty, or the final status is
`needs_decisions`, you (the AI agent) walk the user through **six
questions, in order**: pick an LLM, pick an embedding service, get a
B 站 cookie, ask whether Xiaohongshu likes/favorites may be used, then
ask whether Douyin post/favorite/like/follow signals may be used, then
ask whether YouTube history/subscriptions/likes may be used.
Each question must have a clear default — most users will accept it.

`agent_bootstrap.py` is intentionally non-interactive. If credentials
are already present but you did not pass an explicit embedding choice
and explicit source choices (`--yes-xhs` / `--no-xhs` plus
`--yes-douyin` / `--no-douyin` plus
`--yes-youtube` / `--no-youtube`), it returns
`status=needs_decisions` and **does not run init**. Ask the missing
questions, then re-run bootstrap with those flags.

v0.3.95+ embedding safety net (`should_auto_wire_embedding`): when
`[llm.embedding].provider` would otherwise stay empty — e.g. the chat
provider is Claude / DeepSeek / OpenRouter (which can't embed) and you
never passed `--embedding-provider` — bootstrap auto-writes
`provider=ollama, model=bge-m3` and pulls the model, so semantic dedup
isn't silently disabled (the symptom is recommendations repeating
near-identical content under different ids). This is skipped under
Docker (the container can't reach the host's Ollama at `localhost`) and
when you explicitly disable embedding via `--embedding-provider ""`.
Emits `embedding_auto_ollama` in the progress stream when it fires.
In Docker human one-line installs, the default `ollama` + `bge-m3`
embedding choice is instead pointed at the compose sidecar
`http://ollama:11434/v1` before config is copied into `/app/runtime`.

### Step 1 — Pick an LLM service

Tell the user, in plain Chinese (or the conversation's language):

> 「OpenBiliClaw 需要一个语言模型来理解你的兴趣、写推荐文案。你可以选:」

Present **seven top-level options**. Keep Base URL / model-name details
inside option 2's submenu; do not ask those advanced fields unless the
user chooses an OpenAI-compatible gateway / preset path.

> 模型清单以 **2026-05 当前线上**为准,各家在更新。

| 选项 | 默认模型 | 适合谁 | 是否需要 API Key | 钱 / 速度 |
|---|---|---|---|---|
| 1. **DeepSeek** ★第一推荐(极便宜 / 国内可直连) | `deepseek-v4-flash`(可选 `deepseek-v4-pro`;旧 `deepseek-chat`/`deepseek-reasoner` **2026/07/24 弃用**) | 想几毛钱体验完整功能 | ✅ 需要 | ¥0.001 / 千 token,几乎免费 |
| 2. **★ 中转站 / OpenAI 协议兼容服务** ★第二推荐 | 选 preset 后自动填 | **国内买中转站 / OneAPI Key 的人走这个**;也覆盖 Kimi / MiniMax / 通义 / 智谱 / Yi 官方 + Azure / vLLM / LMStudio | 看服务 | 看服务 |
| 3. **OpenAI 官方** | `gpt-5-nano`(最便宜的 GPT-5;可选 gpt-5.4-nano / -mini / gpt-5.5 旗舰) | 已有 sk- 开头 Key | ✅ 需要 | $0.05/M(nano) ~ $30/M(5.5-pro) |
| 4. **Gemini 官方** | `gemini-2.5-flash`(稳定;可选 gemini-3-flash-preview / gemini-3.1-pro-preview 旗舰 Public Preview 需付费项目) | Google AI Studio 申请 Key | ✅ 需要 | 免费档每天 1500 次 |
| 5. **Claude 官方** | `claude-sonnet-4-6`(1M ctx;可选 claude-haiku-4-5 便宜 / claude-opus-4-7 旗舰) | Anthropic console | ✅ 需要 | $3-$75/M,按 token,质量高 |
| 6. **OpenRouter** | `openai/gpt-5-nano`(格式 `<vendor>/<model>`) | 一个 Key 跑多家 | ✅ 需要 | 按调用计费 |
| 7. **本地 Ollama**（完全免费 / 离线 / 不要 Key） | `qwen2.5:7b`(中文好;可选 llama3.2 / gemma2 / mistral / deepseek-r1) | 16GB+ 内存,能接受 1–3 分钟首次响应 | ❌ 不需要 | ✅ 免费 / ⚠️ CPU 推理慢 |

> ⚠️ **不要把选项 3 (OpenAI 官方) 和选项 2 (协议兼容) 混淆**:走 OpenAI API 官方端点选 3;走任何"OpenAI 协议兼容"的第三方 / 自建服务选 2。

**AI agent 决策建议**: **默认引导 #1 DeepSeek**(几毛钱搞定);用户明确说"我有中转站 Key / OneAPI / 通义 / 智谱 / Kimi / MiniMax / Yi / Azure / vLLM 等任何 OpenAI 兼容服务"→ 引导 #2(进子菜单后再细分);用户明确说"用 OpenAI / Gemini / Claude 官方"才走 #3-5;Ollama 仅在用户明确要求"本地 / 离线"时引导。

**选项 2 的核心场景:你买了第三方中转站 / OneAPI 的 Key**,想用人民币付钱跑 OpenAI / Claude / 国产模型 —— 这是国内绝大多数用户用这个选项的真正原因。子菜单 9 个 preset 中,**第 1 个就是中转站(默认)**:

| 子菜单# | 服务 | Base URL | 默认模型 / 备选 |
|---|---|---|---|
| ★ 1 | **中转站 / OneAPI / 公司团队 LLM 网关 (大多数人选这个)** | 用户自填 | 默认 `gpt-5-nano`;按你充值的那家给你的模型清单填(中转站常代理 OpenAI / Claude / 国产) |
| 2 | **Kimi (Moonshot AI 月之暗面) 官方** | `https://api.moonshot.ai/v1` | `kimi-k2.6`(最新 / 256K ctx / 多模态)。⚠ 旧 K2-series **2026/05/25 停服**;旧 `moonshot-v1-*` 也将停 |
| 3 | **MiniMax 官方** | `https://api.minimax.io/v1` | `MiniMax-M2.7`(4/2026 / 228K ctx / $0.30 ~ $1.20 per M) |
| 4 | **通义千问 (阿里 DashScope) 官方** | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus`(自动跟最新快照,当前 → qwen3.6-plus) / `qwen-flash`(便宜) / `qwen-max`(旗舰) |
| 5 | **智谱 ChatGLM 官方** | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.7-flash`(1/2026 免费 / 200K ctx) / `glm-5`(2/2026 付费旗舰 / 745B MoE)。注意 base_url 用 `/api/paas/v4` 不是 `/v1` |
| 6 | **零一万物 (Yi) 官方** | `https://api.lingyiwanwu.com/v1` | `yi-medium` / `yi-spark`(便宜) / `yi-lightning`(快) / `yi-large`(旗舰) |
| 7 | **Azure OpenAI** | `https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT` | 用户自填 deployment name(不是底层 gpt-5) |
| 8 | **自建 vLLM / LMStudio / Ollama 网关** | `http://localhost:8000/v1` | 用户自填 HuggingFace 路径(如 `meta-llama/Llama-3.3-70B-Instruct`) |
| 9 | **其它(完全手填)** | 用户自填 | 用户自填 |

> 💡 **AI agent 注意**:
> - 用户说"我有中转站 / OneAPI / 团队网关 / 公司给的 Key"等(国内最常见)→ 选项 2 子菜单 #1 (relay)
> - 用户说"我有 Kimi / 通义 / 智谱 / Yi / Moonshot / MiniMax / Qwen / GLM 官方 Key" → 选项 2 子菜单 #2-6 对应 preset
> - 用户说"Azure OpenAI / 公司 Azure 部署" → 子菜单 #7 (azure)
> - 用户说"自己跑的 vLLM / LMStudio / Ollama OpenAI 兼容 shim" → 子菜单 #8 (self-hosted)
>
> 子菜单选完后,**human one-line installer 会写到 `[llm.openai_compatible]`**（provider=`openai_compatible`,base_url 必填,和 OpenAI 官方配置解耦）。AI-agent 非交互 `--llm-preset` 兼容路径本阶段仍写 `[llm.openai]` 段(provider 字段是 `openai`,底层走 OpenAI Chat Completions 协议),避免破坏既有 agent prompt。子菜单还会**显示服务介绍 + Key 申请链接**,并**预提醒 embedding 怎么办**(Kimi / MiniMax / Yi / 自建 没 embedding endpoint → Phase 3 自动 fallback Ollama bge-m3;Qwen / GLM / Azure / 中转站 有 embedding → Phase 3 高级选项可指向同一 base_url)。

**AI agent 一键非交互式安装(`--llm-preset`)** —— 不走交互菜单,直接传 preset 名给 `agent_bootstrap.py`,Base URL + 默认模型自动从 preset 表里拿。**最常见的中转站场景排第一**:

```bash
# ★ 中转站 / OneAPI (国内最常见 — Base URL + Key 必填,模型默认 gpt-5-nano 可改)
python3 scripts/agent_bootstrap.py --llm-preset relay \
  --llm-base-url https://your-relay.com/v1 \
  --llm-api-key sk-xxx \
  --bilibili-cookie "SESSDATA=..."

# Kimi (Moonshot AI) 官方
python3 scripts/agent_bootstrap.py --llm-preset kimi --llm-api-key sk-xxx ...

# MiniMax 官方
python3 scripts/agent_bootstrap.py --llm-preset minimax --llm-api-key xxx ...

# 通义千问 (DashScope) 官方
python3 scripts/agent_bootstrap.py --llm-preset qwen --llm-api-key sk-xxx ...

# 智谱 ChatGLM 官方
python3 scripts/agent_bootstrap.py --llm-preset zhipu --llm-api-key xxx.xxx ...

# 零一万物 Yi 官方
python3 scripts/agent_bootstrap.py --llm-preset yi --llm-api-key xxx ...

# Azure OpenAI (Base URL 是 deployment 全路径, 模型 = deployment name)
python3 scripts/agent_bootstrap.py --llm-preset azure \
  --llm-base-url 'https://YOUR.openai.azure.com/openai/deployments/YOUR-DEP' \
  --llm-api-key xxx --llm-model YOUR-DEP ...

# 自建 vLLM / LMStudio (默认 base_url 是 localhost:8000/v1, 模型必填)
python3 scripts/agent_bootstrap.py --llm-preset self-hosted \
  --llm-model meta-llama/Llama-3.3-70B-Instruct ...
```

`--llm-base-url` / `--llm-model` 单独传时会**覆盖**对应 preset 字段(per-field override),给你 escape hatch 而不强制走 preset 默认。`--llm-preset` 隐式锁 `--provider=openai`,显式传不同 provider 会冲突报错。

**Why DeepSeek default, not Ollama**: previous versions called Ollama
"推荐新手 / 白嫖" but in practice CPU inference on a 16 GB Mac is slow
enough that users think the install is broken. DeepSeek charges roughly
¥0.001 per thousand tokens — running OpenBiliClaw for a month costs
under ¥1 for most users. That's the actual zero-friction path. Ollama
remains a first-class option for people who genuinely want offline /
no-key setups, but should not be sold as "新手友好".

**Hardware caveat for option 7 (Ollama)**: tell the user upfront —
"本地模型的首次响应会比较慢（CPU 推理），内存建议 16GB 以上。如果你介意等待，
选 1 或 2 更顺。" Don't wave them into Ollama if they have a 4-core
Windows laptop with 8 GB.

### Advanced — OpenAI-compatible self-hosted gateway

**Skip this whole section unless the user explicitly says** "I have a
self-hosted gateway / Azure OpenAI / OneAPI / vLLM / LMStudio / 内网反代".
Most users have no idea what these are — surfacing this option in the
main menu used to confuse people who just wanted GPT-4o.

When the user *does* mention a gateway, ask **all three**:

> 「你的网关需要给我三件套：
>   - **Base URL**：网关的 `/v1` 端点（例：`http://localhost:8000/v1` 或
>     `https://your-gateway.example.com/v1`）
>   - **API Key**：网关要不要鉴权？要的话给我 Key；不要的话填 `none` 或留空
>   - **模型名**：网关上具体部署的是哪个模型？（例：vLLM 上的
>     `meta-llama/Llama-3.1-70B`，Azure 上是你的 deployment 名）」

Run with `--provider openai --llm-base-url <URL> --llm-api-key <KEY> --llm-model <MODEL>`.

> ⚠️ **Switching back from gateway to OpenAI 官方** (v0.3.20+): if
> a previous run wrote a `base_url` into `[llm.openai]` and the user
> later runs `--provider openai` *without* `--llm-base-url`, the
> bootstrap automatically clears the stale base URL so the SDK falls
> back to `https://api.openai.com/v1`. You'll see a `base_url_reset`
> event in the JSON stream. Earlier versions silently kept routing
> to the old gateway.

### Step 2 — Configure the chosen LLM

Once they've picked, only ask the **fields that option actually needs**.

#### Option 1 (DeepSeek, default recommendation):

> 「请给我你的 DeepSeek API Key。从 https://platform.deepseek.com/api_keys
>   创建一个。月度费用通常在几毛钱以内。」

Run with `--provider deepseek --llm-api-key <KEY>` plus the embedding
flags from Step 3. DeepSeek has no embeddings endpoint, so recommend
local Ollama bge-m3 unless the user explicitly wants Gemini / OpenAI
embedding. `--embedding-provider ""` now means "do not enable embedding".

#### Options 3-6 (OpenAI 官方 / Gemini / Claude / OpenRouter):

Substitute the right vendor name and Key URL:

- OpenAI: https://platform.openai.com/api-keys (Key starts with `sk-`)
- Gemini: https://aistudio.google.com/apikey
- Claude: https://console.anthropic.com/ → Settings → API Keys
- OpenRouter: https://openrouter.ai/keys

Run with `--provider <name> --llm-api-key <KEY>` plus the Step 3
embedding flags. Don't ask for Base URL. Embedding is independent from
the primary LLM; if the user wants embedding, pass an explicit
`--embedding-provider` and its model/key fields.

#### Option 3 (Ollama, fully offline / no key):

**You don't need to ask the user to install Ollama themselves.** Since
v0.3.10, `agent_bootstrap.py` auto-installs Ollama (macOS via `brew`,
Windows via `winget`, Linux via the official `install.sh`), starts the
daemon in the background, and pulls the chat model. All you tell the
user is:

> 「我会帮你装 Ollama 和拉模型，需要 1–3 分钟（取决于你的网速）。
>   不需要你做任何事，全程会打印进度。
>   首次推理会比较慢（CPU 跑模型），不是装坏了。」

Then run with `--provider ollama --llm-model llama3` (or
`qwen2.5:3b` for a smaller model on weaker hardware), plus the Step 3
embedding flags. No `--llm-api-key` or `--llm-base-url` needed.

If the auto-install fails (no `brew` on Mac, no `winget` on Windows,
no `sudo` on Linux), the bootstrap emits an `ollama_install_failed`
event with a manual-install URL. Tell the user that exact URL and ask
them to install Ollama from there, then re-run the same bootstrap
command — config already on disk, only the Ollama phase will rerun.

Inside Docker mode the bootstrap **does not** auto-install Ollama for
the chat LLM. If the user chooses chat provider `ollama`, tell them to
run host-side Ollama and set `[llm.ollama] base_url` to
`http://host.docker.internal:11434/v1` so the container can reach it.
This is separate from embedding: the default Docker embedding path uses
the compose sidecar at `http://ollama:11434/v1` and does not require a
host Ollama install.

### Step 3 — Embedding (向量化)

Embedding is the service that turns video titles / descriptions into
vectors so the recommendation pipeline can ask "is this clip
semantically close to ones the user already liked?". It's separate from
the chat LLM, gets called frequently (every reshuffle, every dedup
check), and **the choice has a real effect on recommendation quality**.

Tell the user:

> 「OpenBiliClaw 还需要一个向量化(embedding)服务,把视频标题和简介压成向量,
>   用来做"这条和你之前喜欢的那条是不是同一类"的判断。它和聊天 LLM 是分开的。
>
>   三选一,**不确定就回 1**:
>
>   1. **本地 Ollama bge-m3**(默认推荐 / 免费 / 离线 / 不消耗主 LLM 配额)
>      —— 我会自动装 Ollama 并拉 568MB 的 bge-m3 模型
>      —— 多语言效果在开源模型里属于第一档,日常推荐够用
>
>   2. **云端 Gemini embedding**(质量更高 / 跨语言更稳)
>      —— 用 Google 的 `gemini-embedding-001`,在中英混合、长文本、
>         小众词上比本地 bge-m3 略好,推荐能更准一些
>      —— 需要一个 Gemini API Key(免费档每天 1500 次,日常用足够)
>      —— 适合追求推荐质量、能去 Google AI Studio 拿 Key 的人
>
>   3. **暂不启用 embedding**
>      —— 可以先跳过，推荐仍能跑；语义去重 / 相似度能力会降级
>      —— 之后可在设置页或 `openbiliclaw setup-embedding` 单独开启
>
>   日常使用选项 1 完全够用;如果你已经选了 Gemini 当主 LLM,选项 3 等同于
>   选项 2,免费额度通常一天用不完。」

**Mapping the user's answer to bootstrap flags**:

| 用户选 | 命令行参数 | 备注 |
|---|---|---|
| 1 (本地 Ollama, 默认) | `--embedding-provider ollama --embedding-model bge-m3` | bootstrap 会自动装 Ollama + 拉 bge-m3 |
| 2 (Gemini) | `--embedding-provider gemini --embedding-model gemini-embedding-001 --embedding-api-key <KEY>` | 用户已有 Gemini Key 就用现有的;没有就引导去 https://aistudio.google.com/apikey 拿 |
| 3 (暂不启用 embedding) | `--embedding-provider ""` | 这是“我问过用户,用户选择不启用 embedding”的显式记录；bootstrap 不会自动改写为其它 provider |

**Special case — Gemini Key reuse**: if the user picks option 2 *and*
already configured Gemini as their primary LLM, you may reuse the same
key value, but still pass it as `--embedding-api-key <KEY>`. Embedding
credentials are stored in `[llm.embedding]`, not borrowed from
`[llm.gemini]`.

**Safety net (no-op for the agent)**: even when the user picks option 3
or skips entirely, the registry's runtime fallback chain
(`build_embedding_service` in `src/openbiliclaw/llm/registry.py`) still
catches the case where the configured provider has no embeddings
endpoint and falls through ollama → gemini → openai. The chain is the
last line of defence, not the primary UX.

### Step 4 — B 站 Cookie

> 🚨 **Hard Rule 1 applies here.** Read it again before writing your
> reply. The single most common install regression is an agent that
> paraphrases this step down to "请把 B 站 Cookie 发给我" with no
> mention of the extension. Don't be that agent.

**Render the prompt below approximately verbatim.** It is fine to
translate to the conversation's language, but **all five lettered
points (A.1 + B.1–B.4) must appear**, and **A. must come first**.

```
==== render this to the user (exact text, do not summarise) ============
「OpenBiliClaw 需要你的 B 站登录态（Cookie）来拉你的观看历史 + 调 B 站 API。
 Cookie 只存在你本机，不会上传任何地方。

 两种方式（任选其一，强烈推荐 A）：

 A. 装浏览器扩展（推荐，零配置）
    下载: https://github.com/whiteguo233/OpenBiliClaw/releases
    装好后，确保你已登录 B 站（如果没登就去登）。扩展会在几秒内
    自动把 Cookie 推到本地后端，之后 Cookie 过期/续签也会自动同步。
    选这个就什么都不用贴给我，等我从 /api/runtime-status 看到
    bilibili_cookie_synced 即可。

 B. 手动贴 Cookie（不想装扩展时的兜底）
    1. 用 Chrome / Edge / Firefox 登录 https://www.bilibili.com
    2. 按 F12 → Network 标签 → 刷新 → 点任意 bilibili.com 请求
    3. Headers 区域找到 cookie: 这一行，右键复制整行 value
    4. 把那一长串（含 SESSDATA / bili_jct / DedeUserID）粘给我」
==== end of prescribed text =============================================
```

**Background (don't render this part to the user, this is just for
your understanding):** since v0.3.12 the extension auto-syncs the
B 站 cookie to the backend on install — `chrome.cookies.onChanged` →
`POST /api/bilibili/cookie` → backend validates against B 站 nav and
persists. The F12 dance is genuinely a fallback path now: most users
hit it only because their AI agent forgot to mention option A.

**If user picks A**: don't pass `--bilibili-cookie` to bootstrap. The
v0.3.20+ install.sh status block will explicitly print
`OpenBiliClaw backend ready — waiting for browser extension to sync
B站 Cookie` in **green** when this is the only thing missing — this is
the success state, not a failure. (Earlier versions printed yellow
`partial / credentials still missing` here, which routinely scared
users into thinking the install crashed.) Tell the user:

> 「我已经把后端跑起来了。现在请你装扩展（链接 ↑），登录 B 站，
>   等几秒——扩展会自动把 Cookie 推过来。然后我帮你跑 `openbiliclaw init`
>   完成画像生成 + 首轮发现（2-5 分钟）。」

Then poll `GET /api/runtime-status` (or watch for the
`bilibili_cookie_synced` event on `ws://127.0.0.1:8420/api/runtime-stream`)
to detect when the cookie has arrived. If the original bootstrap wait
timed out, re-run the printed `agent_bootstrap.py` command so service
checks and source choices still gate init:

```bash
python3 scripts/agent_bootstrap.py --mode docker --interactive-confirm --wait-for-extension-cookie
```

**If user picks B**: collect the cookie string, run with
`--bilibili-cookie "<full cookie string>"` plus the explicit embedding
and source flags from the user's answers — bootstrap auto-runs
init once everything's present.

### Step 5 — Bilibili init signal limits

Before any non-interactive auto-init, confirm:

> 「B 站初始化默认导入最近 300 条观看历史、最多 300 条收藏、最多 300 个关注 UP。
> 历史保持 300；收藏和关注要改上限吗？直接回车就是 300，填 0 就跳过对应信号。」

Map answers to:

| 项 | 用户回答 | 命令行参数 |
|---|---|---|
| 收藏上限 | 回车 / 不确定 | 省略或 `--bilibili-favorite-limit 300` |
| 收藏上限 | 数字 N | `--bilibili-favorite-limit N` |
| 关注上限 | 回车 / 不确定 | 省略或 `--bilibili-follow-limit 300` |
| 关注上限 | 数字 N | `--bilibili-follow-limit N` |

Human-run `install.sh` / `install.ps1` pass `--interactive-confirm`, so
`agent_bootstrap.py` will ask these two numbers directly and pass them into
`openbiliclaw init`.

### Step 6 — Source data opt-in

Before any non-interactive auto-init, ask:

> 「要把你的小红书收藏 / 点赞也混进初始画像吗？这能让跨平台口味更准，
> 但会让扩展打开小红书页面抓取这些信号。默认不启用；你明确说要用我才开。」

Then ask separately:

> 「要把你的抖音发布 / 收藏 / 点赞 / 关注也混进初始画像吗？这会让抖音口味进入画像，
> 但会让扩展打开抖音页面执行拉取；扩展也会把 douyin.com Cookie 同步给后续 discovery，search / hot / feed discovery 则优先复用登录浏览器插件签名桥。
> 默认不启用；你明确说要用我才开。」

Then ask separately:

> 「要把你的 YouTube 观看历史 / 订阅 / 点赞也混进初始画像吗？这会让长视频口味进入画像，
> 但会让扩展打开 YouTube 页面执行拉取。默认不启用；你明确说要用我才开。」

Map each answer to exactly one bootstrap flag:

| 源 | 用户回答 | 命令行参数 |
|---|---|---|
| 小红书 | 明确同意 | `--yes-xhs` |
| 小红书 | 拒绝 / 没有小红书 / 不确定 / 没回答 | `--no-xhs` |
| 抖音 | 明确同意 | `--yes-douyin` |
| 抖音 | 拒绝 / 没有抖音 / 不确定 / 没回答 | `--no-douyin` |
| YouTube | 明确同意 | `--yes-youtube` |
| YouTube | 拒绝 / 没有 YouTube / 不确定 / 没回答 | `--no-youtube` |

Do not omit these flags. Omitting any source means the agent never asked; bootstrap
will pause with `status=needs_decisions` instead of running init.

### Putting it all together — example commands

The shape of the command depends on what the user picked at each step.
Match each example to the user's actual answers — don't copy-paste blindly.

**默认推荐路径** (DeepSeek + 选项 1 本地 Ollama embedding + 扩展 Cookie)：

```bash
python3 scripts/agent_bootstrap.py \
  --provider deepseek \
  --llm-api-key sk-... \
  --embedding-provider ollama \
  --embedding-model bge-m3 \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

Pass embedding flags explicitly because the user actively picked option 1 —
this records their choice and survives a future primary-LLM swap. The
bootstrap auto-installs Ollama and pulls `bge-m3` in the same run.
Cookie comes via the extension after the backend is up; don't ask the
user to F12 if you can lead them to the extension first.

**质量优先路径** (Gemini 主 + 选项 2 Gemini embedding + 扩展 Cookie)：

```bash
python3 scripts/agent_bootstrap.py \
  --provider gemini \
  --llm-api-key AIza... \
  --embedding-provider gemini \
  --embedding-model gemini-embedding-001 \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

Note: no `--embedding-api-key` because the same Gemini API key the
user already gave for the primary LLM is reused. The free tier
(1500 req/day) covers daily personal use comfortably.

**完全离线路径** (Ollama 主 + 选项 1 Ollama embedding + 扩展 Cookie)：

```bash
python3 scripts/agent_bootstrap.py \
  --provider ollama \
  --llm-model llama3 \
  --embedding-provider ollama \
  --embedding-model bge-m3 \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

**"暂不启用 embedding" 路径** (选项 3)：

```bash
python3 scripts/agent_bootstrap.py \
  --provider deepseek \
  --llm-api-key sk-... \
  --embedding-provider "" \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

When no `--embedding-*` flag is passed, bootstrap leaves embedding as a
pending decision. When `--embedding-provider ""` is passed, bootstrap
records the explicit choice to leave embedding disabled. It does not
follow or auto-rewrite based on the primary LLM.

**自建网关路径** (Advanced — only when user explicitly mentions a gateway)：

```bash
python3 scripts/agent_bootstrap.py \
  --provider openai \
  --llm-base-url http://localhost:8000/v1 \
  --llm-api-key sk-or-none \
  --llm-model meta-llama/Llama-3.1-70B-Instruct \
  --embedding-provider ollama \
  --embedding-model bge-m3 \
  --no-xhs \
  --no-douyin \
  --no-youtube
```

Embedding explicitly pinned to local Ollama because most self-hosted
gateways (vLLM, LMStudio) don't expose `/v1/embeddings`; relying on
the runtime fallback would still work but adds a startup warning.

> ⚠️ **Do NOT pass `--skip-init`** here. The point of running the
> bootstrap with credentials is to reach a usable state. When all
> credentials are present, `--skip-init` is absent, and all init
> decisions are explicit (`--embedding-*` plus source flags:
> `--yes-xhs` / `--no-xhs`, `--yes-douyin` / `--no-douyin`,
> and `--yes-youtube` / `--no-youtube`),
> `agent_bootstrap.py` will first verify the configured LLM provider and
> embedding service. Only if both checks pass will it automatically run
> `openbiliclaw init`: it pulls the user's Bilibili history, generates
> the soul profile, and runs the first content discovery pass. Without
> explicit decisions, bootstrap returns `status=needs_decisions` and
> waits for you to ask the user.
>
> If bootstrap emits `status=service_check_failed`, init has deliberately
> **not** run. Surface the failing service and error to the user, help
> them fix the provider/API key/base_url/model/Ollama issue, then re-run
> the same bootstrap command without `--skip-init`.

After running, **always**:

1. Curl the `Health URL` from the status block to confirm `200 OK`.
2. Report the final state to the user, including:
   - "✅ 后端已启动，监听 http://127.0.0.1:8420"
   - "✅ 配置已写入"
   - "✅ 初始化已完成 —— 已拉取你的 B 站历史，按你的同意混入小红书 / 抖音 / YouTube 信号，生成画像并跑了首轮内容发现"
   - "👉 下一步：装浏览器扩展（链接）来看推荐"

**`init` takes 2-5 minutes on first run** (real LLM calls + real
Bilibili / optional Xiaohongshu / optional Douyin / optional YouTube fetches). Tell the user upfront so they don't think it's
hung. The bootstrap streams init's stdout so progress is visible, and
also emits `BOOTSTRAP_STATUS` events with `status=progress` and
`message=init_progress` for key milestones (`1/4`, `2/4`, `3/4`,
`4/4`, discovery refill progress). AI agents must relay those progress
events to the user instead of staying silent until `init_complete`.

### Init 期间会问用户:B 站上限与小红书 / 抖音 / YouTube 数据是否加入

`openbiliclaw init` 在拉 B 站数据前会确认 B 站收藏 / 关注初始化上限：
默认收藏最多 300 条、关注 UP 最多 100 人；用户直接回车即接受默认，输入
自定义数字会透传到 `--bilibili-favorite-limit` / `--bilibili-follow-limit`，
输入 `0` 可跳过对应信号。

`openbiliclaw init` 在拉 B 站数据**之前**会弹一个交互式问题:是否把
小红书的收藏 / 点赞混进画像。三种状态:

- **交互式终端 + 没有任何 flag**:打印小红书接入说明 + 准备清单
  (装扩展、登录小红书、浏览器开着),用户回 Y/N。回 Y 后再确认
  "准备好了吗",回车继续
- **`openbiliclaw init --no-xhs`**:跳过提问 + 跳过 enqueue,只用
  B 站数据建画像。给"我有 B 站没小红书"的用户一个干净 opt-out
- **`openbiliclaw init --yes-xhs`**:跳过提问直接启用,适合脚本化
- **`OPENBILICLAW_NO_XHS=1` 环境变量**:同 `--no-xhs`,用于永久跳过
- **直接调用 `openbiliclaw init` 的非交互式终端(管道 / CI)**:
  CLI 本身不会弹提问,因此脚本必须传 `--yes-xhs` 或 `--no-xhs`
  才是可审计的行为。
- **通过 `agent_bootstrap.py` 自动 init**:bootstrap 会强制要求
  `--yes-xhs` / `--no-xhs` 二选一。没传就返回
  `status=needs_decisions`,不会运行 init。

随后 `init` 会单独问抖音发布 / 收藏 / 点赞 / 关注是否加入画像：

- **`openbiliclaw init --no-douyin`**:跳过提问 + 跳过 enqueue,只用
  B 站(+小红书,如启用)数据建画像。
- **`openbiliclaw init --yes-douyin`**:跳过提问直接启用,适合脚本化。
- **`OPENBILICLAW_NO_DOUYIN=1` 环境变量**:同 `--no-douyin`,用于永久跳过。
- **直接调用 `openbiliclaw init` 的非交互式终端(管道 / CI)**:
  CLI 默认跳过抖音；脚本化安装仍必须传 `--yes-douyin` 或
  `--no-douyin` 让行为可审计。
- **通过 `agent_bootstrap.py` 自动 init**:bootstrap 会强制要求
  `--yes-douyin` / `--no-douyin` 二选一。

最后 `init` 会单独问 YouTube 观看历史 / 订阅 / 点赞是否加入画像：

- **`openbiliclaw init --no-youtube`**:跳过提问 + 跳过 enqueue,只用
  B 站(+其他已启用源)数据建画像。
- **`openbiliclaw init --yes-youtube`**:跳过提问直接启用,适合脚本化。
- **`OPENBILICLAW_NO_YOUTUBE=1` 环境变量**:同 `--no-youtube`,用于永久跳过。
- **直接调用 `openbiliclaw init` 的非交互式终端(管道 / CI)**:
  CLI 默认跳过 YouTube；脚本化安装仍必须传 `--yes-youtube` 或
  `--no-youtube` 让行为可审计。
- **通过 `agent_bootstrap.py` 自动 init**:bootstrap 会强制要求
  `--yes-youtube` / `--no-youtube` 二选一。

**关键:接入会前台抢焦点**。`max_scroll_rounds=15`(v0.3.64+ CLI 默认,
v0.3.22 ~ v0.3.63 是 3)触发滚动模式,扩展会在用户浏览器里
`chrome.tabs.create({active: true})` 打开一个前台 tab(URL:
https://www.xiaohongshu.com/explore),自动跳到用户 profile 页向下滚动
加载收藏 / 点赞,完成后自动关闭。
执行时长视用户实际收藏量决定 — 收藏少的用户在连续 5 轮 stagnant
(滚不出新 note)后 executor 自动早退,不会跑满 15 轮;收藏多的用户
最多 15 轮才能拉满每 scope 300 条上限。
**这不是隐藏 tab**——背景 tab 在小红书上只渲染浅层 wrapper,触发不到
瀑布流懒加载,所以必须前台。告诉用户:
  - 装机过程中会被切走一次焦点,正常,完成后焦点还回来
  - 期间不要关那个 tab
  - 如果不想被抢焦点(比如在演示 / 录屏),让他设
    `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS=0` 改用浅层模式
    (只读初始 state,后台 tab,但只能拿 ~10-20 条)

抖音接入也会前台抢焦点。扩展会打开抖音页面，依次访问发布 /
收藏 / 点赞 / 关注 scope，结合 DOM 和 MAIN-world API harvester
分批回传结果。默认只在用户明确同意时启用；不登录或触发风控时
可能 0 条，但 init 会继续完成。

YouTube 接入同样会前台抢焦点。扩展会打开 YouTube 观看历史 /
订阅 / 点赞页面并从 DOM 读取条目。默认只在用户明确同意时启用；
不登录、页面语言/布局变化或任务仍在后台跑时可能 0 条，但 init
会继续完成。

AI agent 视角:**不要省略这些问题**。一句话安装走的是
`agent_bootstrap.py` 的非交互路径,不会有交互式 prompt 替你兜底。
用户明确同意才传 `--yes-xhs` / `--yes-douyin` / `--yes-youtube`;
其余情况传 `--no-xhs` / `--no-douyin` / `--no-youtube`。

### Per-module overrides（高级，默认不要问）

`--module-override MODULE=PROVIDER:MODEL`（可重复）。模块：`soul` /
`discovery` / `recommendation` / `evaluation`。**只有当用户主动说「我想给画像
用更贵的模型，发现用便宜的」之类的话，才提这个**。否则跳过——大多数用户根本不
关心，多问一项就多一份让人迷惑的可能。

## Optional: local Ollama as the embedding fallback

This is a **post-install opt-in**, not part of the install contract. Mention
it to the user only if they ask about offline operation, embedding-quota
errors, or a no-API-key setup. Steps:

1. User installs Ollama: Mac `brew install ollama && ollama serve &`,
   Windows from `https://ollama.com/download`, Linux
   `curl -fsSL https://ollama.com/install.sh | sh && ollama serve &`.
2. User runs `cd <INSTALL_DIR> && uv run openbiliclaw setup-embedding`.
3. The wizard probes `localhost:11434`, pulls `bge-m3` if missing, and
   writes `[llm.embedding] provider/model/base_url` to `config.toml`.
   Restart the backend after this.

Do NOT run these steps for the user automatically — Ollama install is a
system-level package the user must consent to.

> **Backend version note**: in v0.3.0–v0.3.2 the wizard wrote the
> embedding config but the LLM registry never registered Ollama
> unless `[llm.ollama]` was also populated, so embedding silently
> kept hitting the user's primary cloud provider. Fixed in **v0.3.3**:
> the registry auto-registers Ollama whenever `[llm.embedding]` asks
> for it. If a user reports "I configured ollama but embedding still
> hits Gemini/OpenAI", check their backend version and tell them to
> upgrade to v0.3.3+ and restart.

## Hard rules

1. **Never edit `config.toml` by hand.** Every credential write goes through `scripts/agent_bootstrap.py`.
2. **Never hard-code `http://127.0.0.1:8420/api/health`.** Always use the `Health URL` line from the status block — the port may be different if the user already has another instance running.
3. **Run init by default — DO NOT pass `--skip-init`.** Once all credentials are present, the user's expectation is "the app is ready to use." That means: history pulled, soul profile generated, first discovery pass done. `agent_bootstrap.py` does this automatically after the backend is healthy. Only pass `--skip-init` when the user explicitly says "don't pull my history yet" or you're doing a credentials-only patch on an already-initialized install.
4. **Never use WebFetch on this document.** WebFetch summarises markdown and can drop exact flags. Use Bash `curl -o` + Read instead.

## Deeper reference (for humans, not required)

- `docs/agent-deployment.md` — long-form troubleshooting with the full JSON event reference
- `docs/docker-deployment.md` — manual Docker setup
- `docs/openclaw-quickstart.md` — OpenClaw-specific integration after install
- `scripts/install.sh` — the installer itself (the command above)
- `scripts/agent_bootstrap.py` — the Python contract core invoked by install.sh
