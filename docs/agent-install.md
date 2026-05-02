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

> The leading `[Net.ServicePointManager]...Tls12` line is **required on PowerShell 5.1** (the default that ships with Windows 10/11). PS 5.1 defaults to TLS 1.0/1.1, but GitHub.com only accepts TLS 1.2+. Without the prefix, `iwr` fails with "underlying connection was closed" and the user blames the installer. Users on PowerShell 7+ can omit the prefix. Compatible from v0.3.9 forward — the script itself also re-applies the same setting once it starts running, so any subsequent HTTPS calls (git, pip, uv) inside the script are also covered.

Either command:

1. Clones the OpenBiliClaw repo (default `~/OpenBiliClaw` on Unix, `%USERPROFILE%\OpenBiliClaw` on Windows; override with the `INSTALL_DIR` env var)
2. Auto-detects any existing OpenBiliClaw install under the standard candidate paths (`~/workspace/OpenBiliClaw`, `~/OpenBiliClaw`, `~/projects/OpenBiliClaw`, `~/code/OpenBiliClaw` — same set on both platforms, rooted at `$HOME` / `%USERPROFILE%`) and **reuses** its LLM API keys and Bilibili cookie so the user never has to retype them
3. Installs Python dependencies (`uv sync` preferred, `pip install -e .` fallback)
4. Starts the backend and runs a health check against `/api/health`
5. Prints a self-contained **status block** at the very end of stdout:

```
================================================================
 OpenBiliClaw install complete / partial (credentials missing)
================================================================
Status:      complete | running_with_missing_secrets | needs_secrets | error
Checkout:    <absolute path to the repo>
Reused from: <path>                 (only present when reuse happened)
Health URL:  http://host:port/api/health
Missing:     (none)  |  llm.<provider>.api_key, bilibili.cookie, ...

Next action (required — credentials are missing):
  1. Ask the user for: <exactly the missing items>
  2. Run this command with the values: <exact python3 command>
     (init will run automatically once credentials are filled in;
      do NOT add --skip-init)
  3. Curl the Health URL to confirm.
  4. Report the final state.

 — or —

Next action (init has been run automatically):
  - Verify the backend is healthy: curl -sS <Health URL>
  - See recommendations:    cd <dir> && uv run openbiliclaw recommend
  - View the soul profile:  cd <dir> && uv run openbiliclaw profile
  - Re-run init manually if needed: cd <dir> && uv run openbiliclaw init
================================================================
```

**Follow that block literally.** That's the entire contract.

## Handling missing credentials

When `Missing` is non-empty, you (the AI agent) walk the user through
**three questions, in order**: pick an LLM, pick an embedding service,
get a B 站 cookie. Each question must have a clear default — most users
will accept it. The previous "tell me what an embedding is" framing
was the failure mode; the new framing is "pick a default I prepared
for you, or override it if you have an opinion."

Don't dump all questions at once — ask one at a time, **explain what
each thing does in plain language**, and offer the easy path first.

### Step 1 — Pick an LLM service

Tell the user, in plain Chinese (or the conversation's language):

> 「OpenBiliClaw 需要一个语言模型来理解你的兴趣、写推荐文案。你可以选:」

Present **three top-level options** (the OpenAI-compatible gateway path
is folded into "Advanced" further down — do **not** put it in the
user's main menu unless they explicitly mention having a gateway):

> 模型清单以 **2026-05 当前线上**为准,各家在更新。

| 选项 | 默认模型 | 适合谁 | 是否需要 API Key | 钱 / 速度 |
|---|---|---|---|---|
| 1. **DeepSeek** ★第一推荐(极便宜 / 国内可直连) | `deepseek-v4-flash`(可选 `deepseek-v4-pro`;旧 `deepseek-chat`/`deepseek-reasoner` **2026/07/24 弃用**) | 想几毛钱体验完整功能 | ✅ 需要 | ¥0.001 / 千 token,几乎免费 |
| 2. **★ 中转站 / OpenAI 协议兼容服务** ★第二推荐 | 选 preset 后自动填 | **国内买中转站 / OneAPI Key 的人走这个**;也覆盖 Kimi / MiniMax / 通义 / 智谱 / Yi 官方 + Azure / vLLM / LMStudio | 看服务 | 看服务 |
| 3. **OpenAI 官方** | `gpt-5-nano`(最便宜的 GPT-5;可选 gpt-5.4-nano / -mini / gpt-5.5 旗舰) | 已有 sk- 开头 Key | ✅ 需要 | $0.05/M(nano) ~ $30/M(5.5-pro) |
| 4. **Gemini 官方** | `gemini-2.5-flash`(稳定;可选 gemini-3-flash-preview / gemini-3.1-pro 旗舰) | Google AI Studio 申请 Key | ✅ 需要 | 免费档每天 1500 次 |
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
> 子菜单选完后,bootstrap 会写到 `[llm.openai]` 段(provider 字段都是 `openai`,底层走的是 OpenAI Chat Completions 协议)。子菜单还会**显示服务介绍 + Key 申请链接**,并**预提醒 embedding 怎么办**(Kimi / MiniMax / Yi / 自建 没 embedding endpoint → Phase 3 自动 fallback Ollama bge-m3;Qwen / GLM / Azure / 中转站 有 embedding → Phase 3 高级选项可指向同一 base_url)。

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

**Hardware caveat for option 3 (Ollama)**: tell the user upfront —
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

Run with `--provider deepseek --llm-api-key <KEY>`. The bootstrap will
automatically wire local Ollama bge-m3 for embedding (DeepSeek has no
embeddings endpoint) and pull the model in the same run — see
"Embedding (handled automatically)" below.

#### Option 2 (OpenAI 官方 / Gemini / Claude / OpenRouter):

Substitute the right vendor name and Key URL:

- OpenAI: https://platform.openai.com/api-keys (Key starts with `sk-`)
- Gemini: https://aistudio.google.com/apikey
- Claude: https://console.anthropic.com/ → Settings → API Keys
- OpenRouter: https://openrouter.ai/keys

Run with `--provider <name> --llm-api-key <KEY>`. Don't ask for Base URL.
For Claude / OpenRouter the bootstrap will auto-wire Ollama bge-m3 for
embedding (those backends don't expose embeddings); OpenAI and Gemini
use their own native embedding endpoints.

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
`qwen2.5:3b` for a smaller model on weaker hardware). No `--llm-api-key`
or `--llm-base-url` needed.

If the auto-install fails (no `brew` on Mac, no `winget` on Windows,
no `sudo` on Linux), the bootstrap emits an `ollama_install_failed`
event with a manual-install URL. Tell the user that exact URL and ask
them to install Ollama from there, then re-run the same bootstrap
command — config already on disk, only the Ollama phase will rerun.

Inside Docker mode the bootstrap **does not** auto-install Ollama. The
container talks to the host's Ollama at `host.docker.internal:11434`,
so installing it inside the container would be the wrong target. The
user must run the host-side `ollama` themselves; the bootstrap just
checks `[llm.ollama] base_url`. Tell Docker users to install Ollama
on their host first.

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
>   3. **跟随你的主 LLM**(最省事,但有取舍)
>      —— OpenAI 主模型 → 用 OpenAI 的 text-embedding-3-small(会消耗 OpenAI 配额)
>      —— Gemini 主模型 → 等同于选项 2
>      —— Claude / DeepSeek / OpenRouter 主模型 → 它们没 embedding 接口,
>         会自动回退到选项 1
>
>   日常使用选项 1 完全够用;如果你已经选了 Gemini 当主 LLM,选项 3 等同于
>   选项 2,免费额度通常一天用不完。」

**Mapping the user's answer to bootstrap flags**:

| 用户选 | 命令行参数 | 备注 |
|---|---|---|
| 1 (本地 Ollama, 默认) | `--embedding-provider ollama --embedding-model bge-m3` | bootstrap 会自动装 Ollama + 拉 bge-m3 |
| 2 (Gemini) | `--embedding-provider gemini --embedding-model gemini-embedding-001 --embedding-api-key <KEY>` | 用户已有 Gemini Key 就用现有的;没有就引导去 https://aistudio.google.com/apikey 拿 |
| 3 (跟随主 LLM) | (不传任何 `--embedding-*` flag) | bootstrap 在主 LLM 是 Claude/DeepSeek/OpenRouter 时会自动改写为选项 1 等价配置(发 `embedding_auto_ollama` 事件);其它主 LLM 则用各自的 native endpoint |

**Special case — Gemini Key reuse**: if the user picks option 2 *and*
already configured Gemini as their primary LLM (i.e. you ran
`--provider gemini --llm-api-key sk-...` earlier), don't ask for the
key again. Just pass `--embedding-provider gemini --embedding-model gemini-embedding-001`
without `--embedding-api-key`; the registry shares the `[llm.gemini]`
section.

**Safety net (no-op for the agent)**: even when the user picks option 3
or skips entirely, the registry's runtime fallback chain
(`build_embedding_service` in `src/openbiliclaw/llm/registry.py`) still
catches the case where the configured provider has no embeddings
endpoint and falls through ollama → gemini → openai. The chain is the
last line of defence, not the primary UX.

### Step 4 — B 站 Cookie

Most users haven't done this before. **Don't just say "give me your
Bilibili cookie."** Walk them through it:

**Lead with the extension.** Since v0.3.12 the extension auto-syncs the
B 站 cookie to the backend on install — `chrome.cookies.onChanged` →
`POST /api/bilibili/cookie` → backend validates against B 站 nav and
persists. F12 dance is now the **fallback**, not the primary path.

Tell the user, in this order:

> 「OpenBiliClaw 需要你的 B 站登录态（Cookie）来拉你的观看历史 + 调 B 站 API。
> **Cookie 只存在你本机，不会上传任何地方。**
>
> 两种方式（**任选其一**）：
>
> **A. 装浏览器扩展（推荐，零配置）**
>   下载: https://github.com/whiteguo233/OpenBiliClaw/releases
>   装好后，确保你已登录 B 站（如果没登就去登）。扩展会在几秒内把
>   Cookie 自动推到本地后端，之后 Cookie 过期/续签都会自动同步。
>
> **B. 手动贴 Cookie（不想装扩展时的兜底）**
>   1. 用 Chrome / Edge / Firefox 登录 https://www.bilibili.com
>   2. 按 F12 → Network 标签 → 刷新 → 点任意 bilibili.com 请求
>   3. Headers 区域找到 cookie: 这一行，右键复制整行 value
>   4. 把那一长串（含 SESSDATA / bili_jct / DedeUserID）粘给我」

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
to detect when the cookie has arrived, and run init via:

```bash
docker exec -it openbiliclaw-backend openbiliclaw init   # docker mode
# or
uv run openbiliclaw init                                  # local + uv
```

**If user picks B**: collect the cookie string, run with
`--bilibili-cookie "<full cookie string>"` — bootstrap auto-runs init
once everything's present.

### Putting it all together — example commands

The shape of the command depends on what the user picked at each step.
Match each example to the user's actual answers — don't copy-paste blindly.

**默认推荐路径** (DeepSeek + 选项 1 本地 Ollama embedding + 扩展 Cookie)：

```bash
python3 scripts/agent_bootstrap.py \
  --provider deepseek \
  --llm-api-key sk-... \
  --embedding-provider ollama \
  --embedding-model bge-m3
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
  --embedding-model gemini-embedding-001
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
  --embedding-model bge-m3
```

**"我不想想这个,跟随主 LLM" 路径** (选项 3 / 用户跳过)：

```bash
python3 scripts/agent_bootstrap.py \
  --provider deepseek \
  --llm-api-key sk-...
```

When no `--embedding-*` flag is passed AND the primary is Claude /
DeepSeek / OpenRouter, the bootstrap auto-wires `[llm.embedding]
provider=ollama model=bge-m3` and emits `embedding_auto_ollama`. For
OpenAI / Gemini / Ollama primaries, the embedding follows that
provider's native endpoint. Either way the user has a working
embedding service.

**自建网关路径** (Advanced — only when user explicitly mentions a gateway)：

```bash
python3 scripts/agent_bootstrap.py \
  --provider openai \
  --llm-base-url http://localhost:8000/v1 \
  --llm-api-key sk-or-none \
  --llm-model meta-llama/Llama-3.1-70B-Instruct \
  --embedding-provider ollama \
  --embedding-model bge-m3
```

Embedding explicitly pinned to local Ollama because most self-hosted
gateways (vLLM, LMStudio) don't expose `/v1/embeddings`; relying on
the runtime fallback would still work but adds a startup warning.

> ⚠️ **Do NOT pass `--skip-init`** here. The point of running the
> bootstrap with credentials is to reach a usable state. When all
> credentials are present and `--skip-init` is absent (the default),
> `agent_bootstrap.py` will automatically run `openbiliclaw init` after
> the backend is healthy: it pulls the user's Bilibili history,
> generates the soul profile, and runs the first content discovery
> pass. **Without init, the user has nothing to look at — they'd see an
> empty extension and would have to manually run `openbiliclaw init`
> themselves**, which defeats the point of one-line install.

After running, **always**:

1. Curl the `Health URL` from the status block to confirm `200 OK`.
2. Report the final state to the user, including:
   - "✅ 后端已启动，监听 http://127.0.0.1:8420"
   - "✅ 配置已写入"
   - "✅ 初始化已完成 —— 已拉取你的 B 站历史、生成画像、跑了首轮内容发现"
   - "👉 下一步：装浏览器扩展（链接）来看推荐"

**`init` takes 2-5 minutes on first run** (real LLM calls + real
Bilibili fetches). Tell the user upfront so they don't think it's
hung. The bootstrap streams init's stdout so progress is visible.

### Init 期间会问用户:小红书数据是否加入(v0.3.27+)

`openbiliclaw init` 在拉 B 站数据**之前**会弹一个交互式问题:是否把
小红书的收藏 / 点赞混进画像。三种状态:

- **交互式终端 + 没有任何 flag**:打印小红书接入说明 + 准备清单
  (装扩展、登录小红书、浏览器开着),用户回 Y/N。回 Y 后再确认
  "准备好了吗",回车继续
- **`openbiliclaw init --no-xhs`**:跳过提问 + 跳过 enqueue,只用
  B 站数据建画像。给"我有 B 站没小红书"的用户一个干净 opt-out
- **`openbiliclaw init --yes-xhs`**:跳过提问直接启用,适合脚本化
- **`OPENBILICLAW_NO_XHS=1` 环境变量**:同 `--no-xhs`,用于永久跳过
- **非交互式终端(管道 / CI)**:不弹提问,默认启用,bootstrap 任务
  自带 graceful 降级——扩展没连上时 30s 超时后跳过

**关键:接入会前台抢焦点**。`max_scroll_rounds=3`(v0.3.22+ CLI 默认)
触发滚动模式,扩展会在用户浏览器里 `chrome.tabs.create({active: true})`
打开一个前台 tab(URL: https://www.xiaohongshu.com/explore),自动跳到
用户 profile 页向下滚动加载收藏 / 点赞,10-30s 完成后自动关闭。
**这不是隐藏 tab**——背景 tab 在小红书上只渲染浅层 wrapper,触发不到
瀑布流懒加载,所以必须前台。告诉用户:
  - 装机过程中会被切走一次焦点,正常,完成后焦点还回来
  - 期间不要关那个 tab
  - 如果不想被抢焦点(比如在演示 / 录屏),让他设
    `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS=0` 改用浅层模式
    (只读初始 state,后台 tab,但只能拿 ~10-20 条)

AI agent 视角:**绝大多数情况你不应该手工传任何 flag**。让用户自己
回答 init 的问题——如果用户说 "我没装扩展也不想用小红书",那时再
建议他用 `openbiliclaw init --no-xhs` 跳过提问。

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
   writes both `[llm.embedding]` and `[llm.ollama] base_url` to
   `config.toml`. Restart the backend after this.

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
