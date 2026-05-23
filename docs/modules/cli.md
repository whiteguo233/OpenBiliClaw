# CLI 命令参考

> 所有已实现的 `openbiliclaw` CLI 命令。
>
> 当前 CLI 已统一使用 Rich 输出：
> - 页面标题采用统一标题面板
> - 状态反馈统一为成功 / 警告 / 失败 / 开发中几类状态块
> - 推荐列表使用卡片式展示
> - 用户画像使用分区块展示

## 全局选项

```bash
openbiliclaw [--log-level DEBUG|INFO|WARNING|ERROR] <命令>
```

## 命令一览

| 命令 | 说明 | 状态 |
|------|------|------|
| `config-show` | 显示当前配置和可用 Provider | ✅ |
| `health-check` | 检查 LLM Provider 可用性 | ✅ |
| `auth login` | 设置并验证 B 站 Cookie | ✅ |
| `auth status` | 查看认证状态 | ✅ |
| `login codex` | 导入 / 查看 / 删除 Codex CLI 的 ChatGPT OAuth 凭据（实验） | ✅ |
| `browser status` | 检查 agent-browser 安装 | ✅ |
| `browser open <url>` | 通过浏览器打开页面 | ✅ |
| `browser content <url>` | 获取页面文本内容 | ✅ |
| `start` | 启动本地 API 服务 | ✅ |
| `db-repair` | 检查、备份并修复本地 SQLite 数据库 | ✅ |
| `serve-api` | 启动容器友好的 API 服务 | ✅ |
| `init` | 首次初始化 | ✅ |
| `fetch-douyin` | 单独触发抖音 bootstrap 拉取（不重建画像；默认复用近期任务） | ✅ |
| `fetch-xhs` | 单独触发小红书 bootstrap 拉取（不重建画像；默认复用近期任务） | ✅ |
| `fetch-youtube` | 单独触发 YouTube bootstrap 拉取（不重建画像；默认复用近期任务） | ✅ |
| `import-youtube <path>` | 从 Google Takeout 导入 YouTube 历史 / 订阅 / 点赞 | ✅ |
| `setup-embedding` | 配置本地 Ollama 作为独立 embedding provider（可选） | ✅ |
| `recommend` | 查看推荐 | ✅ |
| `feedback <id> <like\|dislike\|comment>` | 对推荐提交反馈 | ✅ |
| `profile` | 查看用户画像 | ✅ |
| `discover` | 手动触发发现 | ✅ |
| `discover-douyin` | 单独调试抖音 search / hot / feed 内容发现 | ✅ |
| `search-douyin` | 通过浏览器插件调试抖音搜索召回 | ✅ |
| `chat` | 苏格拉底式对话 | ✅ |
| `delight` | 手动查看当前惊喜推荐候选 | ✅ |
| `probe` | 手动查看并确认猜测兴趣方向 | ✅ |

## 详细说明

### `openbiliclaw config-show`

显示当前加载的配置、已注册的 LLM Provider 和最终生效的默认 Provider。
配置概览会直接显示「停止后台 LLM 请求」是否启用，以及「浏览器断开后暂停」是否启用和当前宽限秒数，方便确认插件设置页里的调度配置是否已经写入后端配置。

```bash
$ openbiliclaw config-show
当前配置概览
配置项
Provider 概览
```

### `openbiliclaw health-check`

逐个检查已注册 Provider 的连通性。

```bash
$ openbiliclaw health-check
Provider 健康检查
  openai (default): 可用
  deepseek: 可用
  ollama: 不可用
    原因: connection refused
```

### `openbiliclaw auth login`

交互式或非交互式设置 B 站 Cookie。验证通过后才保存。

```bash
# 交互式
$ openbiliclaw auth login
请输入 B 站 Cookie: SESSDATA=abc; bili_jct=xyz
登录成功
  用户名: alice
  UID: 10086

# 非交互式
$ openbiliclaw auth login --cookie "SESSDATA=abc; bili_jct=xyz"
```

### `openbiliclaw auth status`

检查当前保存的 Cookie 是否有效。

```bash
$ openbiliclaw auth status
认证概览
认证信息
  状态: 已认证
  用户名: alice
  UID: 10086
```

### `openbiliclaw login codex`

管理实验性的 Codex OAuth 凭据。该命令不自建 OAuth 流程，而是复用官方 Codex CLI 的登录态：默认读取 `~/.codex/auth.json`，导入到 `~/.openbiliclaw/codex_auth.json`，供 `[llm.openai].auth_mode="codex_oauth"` 使用。

```bash
# 默认：先尝试导入 ~/.codex/auth.json；没有时调用官方 `codex login` 后再导入
$ openbiliclaw login codex

# 只导入现有 Codex CLI 凭据
$ openbiliclaw login codex --import

# 从指定路径导入
$ openbiliclaw login codex --import --source ~/.codex/auth.json

# 查看状态；不会显示 token 明文
$ openbiliclaw login codex --status

# 删除 OpenBiliClaw 本地副本，不会删除 Codex CLI 自己的登录态
$ openbiliclaw login codex --logout
```

启用方式：

```toml
[llm.openai]
auth_mode = "codex_oauth"
api_key = ""
base_url = ""
```

这是非官方实验路径，OpenAI / Codex CLI 可能随时调整 token 权限或文件格式。`codex_oauth` 下 `base_url` 只能留空或指向 OpenAI 官方 API 域名，避免把 ChatGPT OAuth token 发给第三方代理。

### `openbiliclaw browser status`

检查 agent-browser 是否已安装。

```bash
$ openbiliclaw browser status
浏览器集成状态
浏览器信息
  状态: 已安装
  可执行文件: /usr/local/bin/agent-browser
```

### `openbiliclaw browser open <url>`

通过 agent-browser 打开指定页面。

```bash
$ openbiliclaw browser open https://www.bilibili.com
浏览器已打开
目标地址
  URL: https://www.bilibili.com
```

### `openbiliclaw browser content <url>`

获取指定页面的可见文本内容。

```bash
$ openbiliclaw browser content https://example.com
页面内容
╭─ 页面内容 ─╮
│ Example Domain ... │
╰──────────────╯
```

### `openbiliclaw start`

启动本地 API 服务。默认读取 `config.toml [api]`，新安装默认监听 `0.0.0.0:8420`，方便同局域网手机访问 `/m/`；也支持显式传入 host/port 覆盖配置。

```bash
$ openbiliclaw start

$ openbiliclaw start --host 0.0.0.0 --port 9000
```

适合本地直接运行或调试场景。若只希望本机访问，把 `[api].host` 改为 `127.0.0.1`，或启动时传 `--host 127.0.0.1`。

启动前会先做两件事：

1. 检查 `data/openbiliclaw.db` 是否完整；如果检测到损坏，会拒绝启动并提示先执行 `openbiliclaw db-repair`
2. 在数据库健康且距离上次冷备超过 24 小时时，自动生成一份冷备到 `data/backups/`

如果 `scheduler.pause_on_extension_disconnect=true`，`start` 会在 uvicorn 启动前打印一行 WARN：

```text
WARN extension presence required; backend will pause background LLM work after grace period if no extension client connects
```

这表示 daemon-owned 后台 LLM / embedding 工作需要浏览器插件保持 `runtime-stream` 在线，或仍处于断开后的宽限窗口内；手动 CLI/API 操作不受这个 WARN 影响。

如果配置导致 LLM registry 无法构建，`start` 不会直接让 popup 完全失联，而是以降级模式启动本地 API，并在 uvicorn 启动前打印 `降级模式 / Degraded mode` 面板。面板会列出 `llm_registry_unavailable` 和 blocking issue，并提示打开扩展设置页保存修复配置后重启 daemon。

如果数据库已损坏：

```bash
$ openbiliclaw start
数据库损坏
检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。
```

当前 `start` 不只是提供静态接口，还会顺手启动候选池运行时：

- 监听插件上报的强信号行为
- 在阈值满足时自动刷新推荐候选
- 定时做榜单/探索补货
- 为插件 popup 和 service worker 提供 `/api/runtime-status` 与通知接口

启动后除了现有候选池刷新 loop，还会常驻一个低频账户同步 loop：
- 定期检查观看历史
- 定期检查收藏夹变化
- 定期检查关注 UP 主变化

这些账户侧长期信号会统一转成事件，再进入现有偏好/画像更新链。

当前 `start` 会启动这些接口：

- `GET /api/health`
- `POST /api/events`
- `GET /api/recommendations`

### `openbiliclaw serve-api`

启动更适合 Docker / 脚本调用的 API 服务入口。默认监听 `0.0.0.0:8420`。

```bash
$ openbiliclaw serve-api

$ openbiliclaw serve-api --host 0.0.0.0 --port 8420
```

推荐容器内使用该命令作为启动入口。
当 `scheduler.pause_on_extension_disconnect=true` 时，`serve-api` 与 `start` 一样会在 uvicorn 启动前打印 extension presence WARN，提醒容器后端若没有插件客户端连接，后台 LLM 工作会在宽限期后暂停。
当配置进入降级模式时，`serve-api` 也会打印同一张 `降级模式 / Degraded mode` 面板；容器或脚本可继续通过 `/api/config` 写入修复配置，再重启服务让新 registry 生效。

### `openbiliclaw delight`

手动查看当前可推送的惊喜推荐候选。

```bash
$ openbiliclaw delight
惊喜推荐
【意外契合】阿B 觉得这条你会意外喜欢
  标题: ...
  惊喜分: 0.72
  理由: ...
```

行为说明：

- 先补一次 delight backlog，再从当前池子里取一条“文案已就绪”的候选
- 运行时与 CLI 共用同一套 delight 阈值口径：默认 `0.70`
- 如果当前只有分数、还没生成 `reason/hook`，CLI 不会把它当成可展示候选

### `openbiliclaw probe`

手动列出当前最值得确认的猜测兴趣方向，并支持确认 / 否认 / 多聊聊。

```bash
$ openbiliclaw probe
猜测兴趣方向
1. 城市空间叙事
2. 复杂系统
```

### `openbiliclaw profile`

展示当前灵魂画像。若画像尚未初始化，会明确提示后续执行 `openbiliclaw init`。

```bash
$ openbiliclaw profile
用户画像概览
人格描述
这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人……

核心特质
  理性、谨慎、自驱

价值观
  成长、真实

当前阶段
  稳定积累阶段

深层需求
  被理解、持续成长
```

### `openbiliclaw init`

首次运行编排命令。会顺序执行：

1. 检查运行时 LLM 配置
2. 检查 B 站认证
3. 拉取 B 站历史 / 收藏 / 关注
4. best-effort 等待插件导入小红书初始化信号
5. best-effort 等待插件导入抖音初始化信号
6. best-effort 等待插件导入 YouTube 初始化信号
7. 写入事件层并分析偏好
8. 生成初始画像
9. 按阶段自动补首轮内容池

安装渠道里的首选路径是 `scripts/agent_bootstrap.py` 自动运行 init：Bash / PowerShell / Docker / AI agent 安装会先确认 LLM、embedding、B 站 Cookie 和各来源 opt-in，再触发本命令。直接执行 `openbiliclaw init` 仍保留为高级手动 fallback 和重复初始化入口。

默认初始化信号上限：B 站观看历史最多 300 条、收藏最多 300 条、关注 UP 最多 100 人；小红书 / 抖音 / YouTube 的 `bootstrap_profile` 每个 scope 默认最多 300 条。交互式 `init` 会让用户确认 B 站收藏 / 关注上限，收藏回车使用 300、关注回车使用 100；脚本化场景可传 `--bilibili-favorite-limit N` / `--bilibili-follow-limit N`，传 `0` 表示跳过对应信号。

```bash
$ openbiliclaw init
初始化 OpenBiliClaw
1/4 拉取数据
  浏览历史 300 条 / 收藏 128 个 / 关注 43 人
  小红书 收藏 20 个 / 点赞 20 个 / 浏览记录 0 个
  抖音 发布 24 条 / 收藏 13 个 / 点赞 12 个 / 关注 1 人
  YouTube 观看历史 40 条 / 订阅 12 个 / 点赞 20 个
2/4 分析偏好
3/4 生成画像
4/4 发现内容
补货阶段 1/3: search + related_chain
当前池子 0/100，本轮请求上限 100
阶段完成: 当前池子 28/100，本轮发现 18 条
补货阶段 2/3: trending
当前池子 28/100，本轮请求上限 72
阶段完成: 当前池子 104/100，本轮发现 76 条
初始化完成
初始化摘要
  B 站观看历史: 300 条
  小红书 入库事件: 40 条
  抖音 入库事件: 50 条
  YouTube 入库事件: 72 条
  画像建模总事件: 590 条
  灵魂画像: 已生成
  首轮发现内容: 94 条
  本次画像综合了 428 条 B 站信号 + 40 条小红书信号 + 50 条抖音信号 + 72 条 YouTube 信号。
```

小红书导入依赖浏览器插件在用户已登录的小红书网页里执行 `bootstrap_profile` 任务。后端只入队任务并短暂等待结果，不直接登录或爬取小红书。插件会先定位当前用户 profile，再读取 profile state 里的收藏 / 赞过分组；这里的“浏览记录”指小红书网页自己明确暴露的浏览记录/足迹 state，不是读取 Chrome 浏览器历史，也不会把普通推荐流当成浏览记录。如果后端任务显式设置 `max_scroll_rounds`，插件会按任务 payload 中的 `scroll_wait_ms` 和 `max_stagnant_scroll_rounds` 做有限滚动和停滞判断。如果插件未连接、未登录或页面没有暴露对应 scope，`init` 会继续使用已有 B 站数据完成初始化。

抖音导入同样依赖浏览器插件在用户已登录的 `https://www.douyin.com` 页面里执行 `bootstrap_profile` 任务。后端入队 `dy_tasks`，插件依次访问 `dy_post / dy_collect / dy_like / dy_follow` 四个 scope，content script 结合 DOM、MAIN-world fetch tap 和 API harvester 采集发布 / 收藏 / 点赞 / 关注条目，以 `partial` 批次回写 `/api/sources/dy/task-result`。后端会转换为统一事件：发布 → `view`，收藏 → `favorite`，点赞 → `like`，关注 → `follow`，并带 `metadata.source_platform="douyin"`。`init --yes-douyin` 会把这些事件加入 `analyze_events()` 和 `build_initial_profile()`；插件未连接、未登录或抖音风控返回空数据时，初始化继续使用已有信号完成。后台会复用 6 小时内近期抖音 bootstrap 任务，并用 `source_bootstrap_state.json` 跳过跨任务旧视频 / 关注 identity key。

YouTube 导入依赖浏览器插件在用户已登录的 `https://www.youtube.com` 页面里执行 `bootstrap_profile` 任务。后端入队 `yt_tasks`，插件依次访问 `/feed/history`、`/feed/channels`、`/playlist?list=LL` 三个 scope，读取观看历史、订阅频道和点赞视频，以 `partial` 批次回写 `/api/sources/yt/task-result`。后端会转换为统一事件：观看历史 → `view`，订阅 → `follow`，点赞 → `like`，并带 `metadata.source_platform="youtube"`。`init --yes-youtube` 会把这些事件加入 `analyze_events()` 和 `build_initial_profile()`；非交互式终端默认跳过，`OPENBILICLAW_NO_YOUTUBE=1` 会压过 `--yes-youtube`，避免脚本环境误触发浏览器前台 tab。后台会复用 6 小时内近期 YouTube bootstrap 任务，并用 `source_bootstrap_state.json` 跳过跨任务旧条目。

源开关：

- `--yes-xhs` / `--no-xhs`：跳过小红书交互式提问，直接启用或跳过。
- `--yes-douyin` / `--no-douyin`：跳过抖音交互式提问，直接启用或跳过。非交互式终端默认跳过抖音，脚本化 init 应显式传其中一个。
- `--yes-youtube` / `--no-youtube`：跳过 YouTube 交互式提问，直接启用或跳过。非交互式终端默认跳过 YouTube，脚本化 init 应显式传其中一个。
- `--bilibili-favorite-limit N` / `--bilibili-follow-limit N`：覆盖 B 站收藏 / 关注初始化信号上限，默认各 `300`；`0` 表示跳过对应信号。
- `OPENBILICLAW_NO_XHS=1` / `OPENBILICLAW_NO_DOUYIN=1` / `OPENBILICLAW_NO_YOUTUBE=1`：永久跳过对应源。
- `OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS`：小红书 `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用。
- `OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS` / `OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS`：抖音 / YouTube `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用。

如果当前终端是交互式，且缺少 provider API Key 或 B 站 Cookie，`init` 会直接进入用户友好的引导（v0.3.5+）：

```bash
$ docker exec -it openbiliclaw-backend openbiliclaw init
初始化前配置引导 · 选 LLM、配 Embedding、填 B 站 Cookie

OpenBiliClaw 需要一个语言模型来理解你的兴趣、写推荐文案。
请选一个 LLM 服务：

 #   名称                                  说明
 1   DeepSeek 官方 ★默认推荐                默认 deepseek-v4-flash (V4)。¥0.001/千 token 几乎免费,国内可直连
 2   ★ 第二推荐 — 中转站 / OpenAI 协议兼容服务 买了中转站 Key 选这个。也覆盖 Kimi / 通义 / 智谱 / Yi / MiniMax 官方 / Azure / vLLM
 3   OpenAI 官方                           默认 gpt-5-nano (最便宜的 GPT-5)。api.openai.com,需要 sk- 开头的 Key
 4   Gemini 官方                           默认 gemini-2.5-flash (稳定 / 便宜)。Google AI Studio 申请 Key,免费档每天 1500 次够用
 5   Claude 官方                           默认 claude-sonnet-4-6。Anthropic console,按 token 付费,质量高
 6   OpenRouter 聚合                       默认 openai/gpt-5-nano。一个 Key 跑多家模型,按调用计费
 7   本地 Ollama（完全离线）                默认 qwen2.5:7b (中文好)。不要 Key / 完全免费,但需 16GB+ 内存,CPU 推理首次响应 10-60s

Tip:不确定就选 1 (DeepSeek),¥0.001/千 token 几乎免费,月度通常 ¥0.5-2。已经买了中转站 / OneAPI Key 选 2 (协议兼容);想完全离线选 7 (Ollama,但 CPU 推理慢)。

请输入序号或名称（默认 1=DeepSeek） [1]:

# (随后只问被选中那一项实际需要的字段——
#  例如选 1/3/4/5/6: 只问 API Key + 模型名；
#  选 2: 进协议兼容 preset 子菜单，按需问 Base URL + API Key + 模型名；
#  选 7: 只问模型名，并自动安装 / 启动 Ollama)

Embedding(向量化)服务
把视频标题/简介压成向量,跨视频做相似度对比 —— 决定"这条和你之前喜欢的那条是不是同一类"。和聊天 LLM 是分开的。

 #   方案                                  说明
 1   本地 Ollama bge-m3 ★默认推荐           免费 / 离线 / 不消耗主 LLM 配额(自动装 Ollama + 拉 568MB 模型)
 2   云端 Gemini embedding                 质量略高 / 跨语言更稳;免费档每天 1500 次,日常够用,需 Gemini Key
 3   暂不启用 embedding                    保留独立配置为空;不会跟随主 LLM,也不会自动 fallback
 4   (高级)自定义 OpenAI 兼容服务           vLLM / OneAPI / 自建网关 —— 自填 base_url
 5   (高级)指定其他 provider               手动选 provider + 模型 + 可选 base_url
 0   跳过(不修改当前 embedding 配置)

Tip:不确定就选 1。日常推荐质量已经够用且不消耗主 LLM 配额。想再准一点选 2(Gemini),需要去 https://aistudio.google.com/apikey 拿 Key。
请选择 embedding 方案 [1]:

最后是 Per-module 覆盖（高级，默认可跳过）
（高级，可跳过）是否为单个模块单独指定 provider/model？[y/N]:

初始化前认证引导 · 补齐 B 站认证
为什么需要 B 站 Cookie？
OpenBiliClaw 需要你的 B 站登录态来：
  • 拉你的观看历史（用来训练画像）
  • 以你的身份调 B 站 API 拿视频详情
Cookie 只存在你本机 data/bilibili_cookie.json，不会上传任何地方。

怎么获取：
  1. 用 Chrome / Edge / Firefox 登录 https://www.bilibili.com
  2. 按 F12 打开开发者工具 → 切到 Network（网络）标签
  3. 刷新一下 B 站页面 → 在请求列表点任意一条 bilibili.com 的请求
  4. 右侧 Headers（请求头）区域，找到 cookie: 这一行，右键复制整行的 value
  5. 把那一长串（包含 SESSDATA=...; bili_jct=...; DedeUserID=... 等）粘进来

请粘贴 B 站 Cookie:
```

引导完成后会继续当前初始化流程，不需要再单独执行 `auth login` 或手动改配置。

> **「OpenAI 官方」≠「OpenAI 协议兼容服务」**：向导把这俩拆成独立菜单项。选 3 时只问 API Key，base_url 走 `https://api.openai.com/v1`；选 2 时进入协议兼容 preset 子菜单（中转站 / Kimi / MiniMax / 通义 / 智谱 / Yi / Azure / vLLM / 自定义），按所选 preset 写入 `[llm.openai]` 段。两者底层走的是同一个 OpenAI 协议家族，但用户视角分得很清楚。
>
> **DeepSeek 排第一**是有意为之：它是当前最低摩擦路径，国内可直连且费用接近忽略不计。Ollama 仍保留为完全离线选项，但需要本机算力，首次响应会慢。

首次 `init` 的 discover 阶段可能持续几分钟，因为它会真实访问 B 站接口并调用当前 provider 进行候选打分与表达生成。
当前实现已经对首轮 discover 做了保守受控并发优化，但默认并发上限仍偏保守，优先减少 B 站和 LLM 限流风险。
首轮补货会按 `search + related_chain`、`trending`、`explore` 的顺序推进，并尽量把 fresh 候选池补到至少 `100` 条后再结束。
运行时后台则会继续以 `scheduler.pool_target_count` 为目标持续补货；当前默认目标是 `300`，到达后停止 discover，直到候选池掉回目标以下再继续补货。
运行中会直接打印每一阶段的策略名、当前池子进度和该轮请求上限，便于你判断首轮补货是在持续推进还是确实失败。

如果当前终端不是交互式，`init` 不会等待输入，而是直接报出明确错误；这适合服务器脚本和 CI 场景。

如果 discover 阶段失败，但历史和画像阶段成功，命令会提示“部分完成”，并建议稍后手动执行：

```bash
openbiliclaw discover
```

### `openbiliclaw setup-embedding`

重新进入 embedding 选择向导。`init` 阶段会自动问；只有当时跳过、或要切换方案时才需要主动跑：

```bash
$ openbiliclaw setup-embedding
配置本地 embedding · Ollama + bge-m3

 #   方案                                  说明
 1   本地 Ollama bge-m3 ★默认推荐           免费 / 离线 / 不消耗主 LLM 配额(自动装 Ollama + 拉 568MB 模型)
 2   云端 Gemini embedding                 质量略高 / 跨语言更稳;免费档每天 1500 次,日常够用,需 Gemini Key
 3   暂不启用 embedding                    保留独立配置为空;不会跟随主 LLM,也不会自动 fallback
 4   (高级)自定义 OpenAI 兼容服务           vLLM / OneAPI / 自建网关 —— 自填 base_url
 5   (高级)指定其他 provider               手动选 provider + 模型 + 可选 base_url
 0   跳过(不修改当前 embedding 配置)
请选择 embedding 方案 [1]:
```

每个选项对应的写入路径：

| 选项 | 行为 | 写入字段 |
|---|---|---|
| 1 | 本地 Ollama，自动探测 + 拉取 `bge-m3` | `[llm.embedding] provider="ollama" model="bge-m3" base_url="http://localhost:11434/v1"` |
| 2 | 云端 Gemini embedding，可复用已有 Gemini Key | `[llm.embedding] provider="gemini" model="gemini-embedding-001" api_key="..."` |
| 3 | 暂不启用 embedding | `[llm.embedding] provider="" model=""`；运行时不会跟随主 LLM |
| 4 | 自填 base_url + api_key + model | `[llm.embedding] provider="openai" model="..." base_url="..." api_key="..."` |
| 5 | 选另一个已知 provider 走 embedding | `[llm.embedding] provider="<target>" model="..." base_url="..." api_key="..."` |
| 0 | 跳过 | 不主动写入新 embedding 配置 |

选项 1 时向导会按顺序：

1. 探测 `localhost:11434/api/version`，确认 Ollama 服务在跑
2. 通过 `/api/tags` 检查 `bge-m3` 是否已 pull
3. 没拉就流式 `POST /api/pull`，进度直接打到终端
4. 把 `[llm.embedding] provider="ollama" model="bge-m3" base_url="http://localhost:11434/v1"` 写入 `config.toml`

适合：

- embedding API Key 用完了
- 离线 / 没外网
- 不想再额外申请一份 embedding 服务密钥
- 跨平台一致体验（Mac/Win/Linux 同一 HTTP API）

CPU 即可跑，单次 embedding 约 100-200ms，配合后台 prewarmer 实际"换一批" 仍能稳在 600ms。

如果 Ollama 没安装：

```bash
检测不到 Ollama 服务（localhost:11434）。
  Mac:     brew install ollama && ollama serve
  Windows: 从 https://ollama.com/download 下载安装包
  装好后重新运行本命令即可启用。
```

### `openbiliclaw recommend`

读取推荐缓存，生成朋友式推荐表达，并把已展示条目标记为 `presented=1`。

```bash
$ openbiliclaw recommend
本轮推荐
推荐 1
  标题: 讲透城市与建筑的空间叙事
  UP 主: 城市观察局
  话题标签: 你最近那股想把结构想透的劲头
  推荐理由: 这条会对上你最近那种想把结构想透的劲头，它不是快餐内容，而是会慢慢把结构给你铺开。
  BV号: BV1REC
```

如果当前还没有可推荐内容，会提示先执行：

```bash
openbiliclaw discover
```

### `openbiliclaw feedback <id> <like|dislike|comment>`

为一条已展示的推荐记录写入结构化反馈，可附带备注；`comment` 必须带 `--note`。

```bash
$ openbiliclaw feedback 7 dislike --note "太浅了"
反馈已记录
反馈详情
  推荐ID: 7
  反馈: dislike
  备注: 太浅了

$ openbiliclaw feedback 7 comment --note "方向对，但我想看更深一点。"
```

每次反馈执行以下两个写入操作：

- 更新 `recommendations` 表中的 `feedback_type` / `feedback_note` / `feedback_at`
- 写入一条 `event_type="feedback"` 的事件，供后续记忆系统使用

### `openbiliclaw fetch-douyin`

单独触发抖音 `bootstrap_profile` 拉取，适合 smoke 测试扩展和补拉抖音信号。它只执行“入队 → 唤醒扩展 → 等结果 → 打印 scope counts”，不跑 B 站认证检查、不跑 `analyze_events()` / `build_initial_profile()` / discovery。事件由 daemon 在接收 `/api/sources/dy/task-result` partial 时写入 memory，CLI 自身不会再传播一次，避免重复入库。

```bash
$ openbiliclaw fetch-douyin
抖音 数据拉取
  抖音 发布 24 条 / 收藏 13 个 / 点赞 12 个 / 关注 1 人
  共 50 条事件已由 daemon 写入 memory。
```

默认最多等待扩展回传 `180s`；需要更长排查窗口时可显式加 `--wait-seconds 240`。命令默认复用 6 小时内已有的 pending / in-progress / completed / failed 抖音 `bootstrap_profile` 任务，避免反复打开前台抖音 tab 全量拉发布 / 收藏 / 点赞 / 关注；需要重新拉取时可设 `OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS=0`。

前提：

- `openbiliclaw start` 或 `serve-api` 后端正在运行。
- Chrome 扩展已安装并在线。
- 浏览器已登录 `https://www.douyin.com`。

### `openbiliclaw fetch-xhs`

单独触发小红书 `bootstrap_profile` 拉取，定位与 `fetch-douyin` 相同：用于单源验证 / 补拉，不隐式重建画像。

```bash
$ openbiliclaw fetch-xhs
小红书 数据拉取
  小红书 收藏 20 个 / 点赞 20 个 / 浏览记录 0 个
```

默认最多等待扩展回传 `180s`，与 `init --yes-xhs --yes-douyin` 的单源 collect 窗口保持一致，降低两源连续初始化时小红书未结束就启动抖音的概率。命令默认复用 6 小时内已有的 pending / in-progress / completed / failed `bootstrap_profile` 任务，避免重复打开前台小红书 tab 抓收藏 / 点赞；排查时需要强制重拉可加 `--force`，或用 `OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS=0` 关闭复用窗口。

### `openbiliclaw fetch-youtube`

单独触发 YouTube `bootstrap_profile` 拉取，用于验证浏览器扩展、登录态和 `/api/sources/yt/*` 后端任务桥是否联通。采集范围与 init 相同：观看历史、订阅频道、点赞视频。

```bash
$ openbiliclaw fetch-youtube --wait-seconds 240
YouTube 数据拉取
  YouTube 观看历史 40 条 / 订阅 12 个 / 点赞 20 个
  共生成 72 条事件。
```

这条命令只做单源 smoke / 补拉，不会隐式重建画像。profile 已初始化后，daemon 接收新增 partial 事件时会写入 memory 并进入增量画像更新链路。命令默认复用 6 小时内已有的 YouTube `bootstrap_profile` 任务，避免反复打开前台 YouTube 页面滚动历史 / 订阅 / 点赞；需要重新拉取时可设 `OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS=0`。

### `openbiliclaw import-youtube <path>`

从 Google Takeout 导出的 `.zip` 或解压目录导入 YouTube 观看历史、订阅和点赞数据，适合扩展无法读取旧历史或用户想一次性补齐冷启动信号的场景。

```bash
$ openbiliclaw import-youtube ~/Downloads/takeout.zip --dry-run
导入 YouTube Takeout
  解析完成：
    观看历史  1200 条
    订阅频道  88 个
    点赞视频  320 个
    合计      1608 条事件
```

不带 `--dry-run` 时，命令会把解析出的 YouTube 事件传播到记忆层，并调用 `analyze_events()` 更新偏好画像；它不会重新跑完整 init，也不会自动补推荐池。

### `openbiliclaw discover`

读取当前画像并触发一次内容发现。默认跑 Bilibili 的全部策略并将结果写入 `content_cache`，支持通过 `--source` 切换到 xiaohongshu 关键词生产流程或 douyin discovery，或通过 `--strategy` 限定只跑部分 Bilibili 策略。

```bash
# 默认：Bilibili 全策略
$ openbiliclaw discover
本次内容发现
发现摘要
  发现条数: 12
  缓存状态: 已写入 content_cache
  来源: bilibili
  策略: 全部

# 只跑 search + trending
$ openbiliclaw discover --strategy search,trending --limit 20

# 触发 xiaohongshu 关键词生产（由扩展在后台抓取）
$ openbiliclaw discover --source xiaohongshu
小红书关键词生产
生产摘要
  入队关键词数: 5
  尝试关键词数: 5
  今日预算: 30
  节流开关: 4 小时节流

# 忽略 4 小时节流
$ openbiliclaw discover --source xiaohongshu --force

# 触发 douyin discovery
# Cookie 可由扩展自动同步；下面的环境变量仅用于调试时显式覆盖
$ export OPENBILICLAW_DOUYIN_COOKIE='msToken=...; ttwid=...; ...'
$ openbiliclaw discover --source douyin --limit 20
抖音内容发现
发现摘要
  发现条数: 8
  缓存状态: 已写入 content_cache
  来源: douyin
  策略: dy-plugin-search, dy-plugin-hot-related, dy-plugin-feed
```

选项：

- `--source, -s`：`bilibili`（默认）、`xiaohongshu` 或 `douyin`
- `--strategy, -S`：仅对 Bilibili 生效，可多次传或逗号分隔，取值 `search` / `trending` / `explore` / `related_chain`
- `--limit, -n`：发现结果条数上限，默认 `30`
- `--force`：xiaohongshu 专用，忽略 `XhsTaskProducer` 的 4 小时节流

抖音 discovery 需要 `[sources.douyin].enabled = true`。Cookie 解析顺序是：先读 `cookie_env` 指向的环境变量（默认 `OPENBILICLAW_DOUYIN_COOKIE`，适合调试覆盖），再读浏览器扩展同步的 `data/douyin_cookie.json`。初始化画像的 `init --yes-douyin` 不受这个配置影响，仍走浏览器扩展任务桥。

`search` 子来源优先走浏览器插件签名链路：CLI 入队 `dy_tasks(type="search")`，扩展在已登录抖音会话里打开搜索页，用页面 `byted_acrawler.frontierSign()` 签名搜索 API，候选以 `dy-plugin-search` 进入 discovery；插件任务空 / 超时 / 失败时再回退 direct-cookie search。`hot` 子来源同样优先走插件：后端取 hot board 的 `sentence_id`，扩展打开 `/hot/{sentence_id}` 拿跳转后的 seed aweme，并签名 `/aweme/v1/web/aweme/related/` 拉相关视频，候选以 `dy-plugin-hot-related` 进入 discovery；小批量 hot 请求会少量展开 hot seed，并在累计达到 `--limit` 后提前结束，避免串行页面跳转拖到 `task_timeout`。`feed` 子来源会入队 `dy_tasks(type="feed")`，扩展在已登录首页签名 `/aweme/v1/web/tab/feed/`，候选以 `dy-plugin-feed` 进入 discovery。

需要调试抖音 discovery 子来源时，优先使用独立命令 `openbiliclaw discover-douyin`。它和 `discover --source douyin` 共用同一个 `DouyinDiscoveryService`，但可以显式指定关键词、是否写缓存和是否跳过 LLM 评估：

```bash
# 调试 search + feed，直接看源接口召回，不写 content_cache
$ openbiliclaw discover-douyin \
  --keyword 猫咪,机械键盘 \
  --source search,feed \
  --limit 20 \
  --no-cache \
  --no-evaluate
```

`discover-douyin` 的 `--source` 只接受 `search` / `hot` / `feed`；不传时默认三者都跑。`--keyword` 不传时从 Soul 画像兴趣生成搜索词；`hot` 会自动取 hot board 热词，不需要手动传关键词；`feed` 直接从抖音首页推荐流召回，不需要关键词。

xiaohongshu 渠道并不直接抓取内容，而是调用 `XhsTaskProducer.produce_if_due()` 将 Soul 画像改写成关键词写入 `xhs_tasks` 表，由浏览器扩展的后台调度器在隐藏 Tab 中抓取。若返回 `throttled` 可加 `--force` 重试；若返回 `no_profile` 需先执行 `openbiliclaw init`。

### `openbiliclaw search-douyin`

通过浏览器插件执行抖音搜索 smoke，适合排查 direct-cookie search / hot 被抖音软空时，真实登录浏览器路径能否召回视频候选。

```bash
$ openbiliclaw search-douyin -k 猫 --max-items-per-keyword 10 -w 180
抖音搜索发现
  抖音搜索 10 条候选
  1. 盘点全网那些叛逆的猫咪... 迷惑菌呀
     https://www.douyin.com/video/7219607743328537915
```

行为边界：

- CLI 入队 `dy_tasks(type="search")`，唤醒扩展 dispatcher，等待 `dy_tasks.result_json`。
- 扩展会在已登录抖音浏览器会话里打开搜索页；MAIN-world bridge 使用页面 `byted_acrawler.frontierSign()` 签名搜索 API，再把 `dy_search` 候选回传。
- 默认等待窗口为 `180s`；如果调试机上搜索页首开很慢，可显式加 `--wait-seconds 240`。
- 结果只作为搜索 discovery 候选保存在任务结果中；后端不会把它转换成 memory event，也不会重建画像。独立 `search-douyin` smoke 不写 `content_cache`；正式 `discover-douyin --source search` / `discover --source douyin` 会把同一插件搜索候选纳入 discovery 结果，并在 cache 模式下按 `dy-plugin-search` 写入 `content_cache`。
- 如果返回 0 条，优先检查是否有多个加载扩展的 Chrome 实例抢任务、当前浏览器是否登录抖音，以及 debug 中 `ui_triggered / api_items_harvested / dom_items_harvested`。

如果画像尚未初始化，会提示先执行：

```bash
openbiliclaw init
```

### `openbiliclaw chat`

进入持续对话模式，复用 `SocraticDialogue` 的多轮历史。输入 `exit`、`quit` 或空行可结束。聊天内容会先记录为 `dialogue` 事件，并以受控方式积累到长期理解候选中，不会因为一句话立刻改写画像。

```bash
$ openbiliclaw chat
苏格拉底式对话
你：我最近总在刷讲结构的视频。
阿花：我听见你在说，你现在在意的可能不只是内容本身，而是想把事情看得更透一点。
你：exit
阿花：对话结束。
```

如果画像尚未初始化，会提示先执行：

```bash
openbiliclaw init
```

### `openbiliclaw start`

启动本地后端 API 服务，默认监听 `127.0.0.1:8420`，供浏览器插件或本地调试调用。

启动前会先做两件事：

1. 检查 `data/openbiliclaw.db` 是否完整；如果检测到损坏，会拒绝启动并提示先执行 `openbiliclaw db-repair`
2. 在数据库健康且距离上次冷备超过 24 小时时，自动生成一份冷备到 `data/backups/`

```bash
$ openbiliclaw start
启动 OpenBiliClaw
API 服务
  正在启动本地后端，默认监听 127.0.0.1:8420。
```

如果数据库已损坏：

```bash
$ openbiliclaw start
数据库损坏
检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。
```

### `openbiliclaw db-repair`

显式检查并修复本地 SQLite 数据库。命令遵循”先检查、先备份、后修复”的顺序：

1. 运行完整性检查
2. 若数据库正在被进程占用则拒绝继续
3. 备份 `openbiliclaw.db` 与可选的 `openbiliclaw.db-wal`
4. 尝试恢复到新的 repaired 副本
5. 验证 repaired 副本通过后，再切换正式库

```bash
$ openbiliclaw db-repair
数据库已恢复并完成切换。
备份文件: data/backups/openbiliclaw-20260315-020000.db
恢复副本: data/openbiliclaw.repaired.db
```

如果数据库本来就是健康的，命令会直接退出并提示无需修复；如果仍被运行中服务占用，会返回非零退出码并列出占用进程。

### Stub 命令的输出约定

当前仍是 stub 的命令会统一使用”开发中”占位态输出，避免与真实错误混淆，并会附带建议的下一步命令。
