# 变更日志

> 按里程碑记录各阶段交付内容。每次分支合回 main 时追加条目。

---

## 未发布

- **修复 main 分支 CI 自 8 月 20 日起持续红灯的测试问题**：架构总览图迁出 README（80eb6d92）后，文档一致性测试同步改为读取 `docs/architecture-overview(.en).md`；`test_mobile_web_view_models` 修复一处 E501 超长行；`soul_engine` / `web_guided_init` 的测试桩补齐 d8f63745 新增的 `llm_concurrency` 参数。纯测试改动，无运行时行为变化。

- **修复普通视频点击被误记为点踩（issue #205，含真实浏览器 A/B 验证）**：#200 的长度守卫只保护了 textContent，`aria-label`/`title` 路径仍会把标题含“不喜欢/不感兴趣”的普通卡片点击记成 `feedback(dislike)`——真实 bilibili.com 搜索页 A/B 实测：修复前构建点 3 张真实卡片产生 3 条误报，修复后 0 条。中文平台适配器（bilibili/douyin/zhihu）统一改为「≤8 字符短标签 + className token 匹配」（真实控件标签最长 6 字）；英文平台把动词锚定到真实控件——YouTube 控件 aria-label 动词开头（"Like this video…"/“不喜欢此视频”），结果卡片是「标题 by 作者」句式，故 aria 用动词前缀匹配、卡片文案封顶 12 字符；Reddit 投票只认 aria/class 锚定的 downvote/upvote。真实 YouTube A/B：修复前点 3 张标题含 "dislike" 的结果卡片产生 3 条误报（词边界版仍有 13/48 卡片命中，已改锚定后归零），修复后 0 条且 watch 页真实 dislike 控件正向采集正常。kernel 对 DOM 点踩新增按控件的奇偶点击追踪：第二次点击识别为取消（记 `retraction` 而非新 dislike），10 分钟无操作重置计数。awareness 双 system prompt 新增「负反馈一致性」规则：笔记声称点踩时必须引用真实的负反馈事件。`docs/modules/extension.md` 与 `docs/modules/llm.md` 已同步。
- **修复移动 Web 加载更多推荐时的卡顿感**：`handleAppend()` 不再在插入新卡片前 `await` 整批封面预解码（原来后端返回后还要等最多 3 秒），而是先插入卡片、再后台预热前 4 张封面，其余封面保持 `loading="lazy"` 并交给滚动预热 observer 在接近视口时补拉；`renderCard()` 改为只对列表前 4 张卡使用 eager 封面，避免一次 append 10 张卡同时抢图片代理与解码资源。`appendRecommendations()` 补上 `DEFAULT_READ_TIMEOUT_MS` 请求超时；加载更多失败不再把 `autoAppendExhausted` 永久置为 true，而是在 header 显示「加载更多失败，请稍后重试」并允许下次滚动/点击重试。
- **新增 Serply 作为 query inspiration 搜索后端**：`[discovery]` 新增 `serply_api_key`，填写后 `SerplyInspirationProvider` 直连 [serply.io](https://serply.io) 的 `GET /v1/search`；`inspiration_search_backends` 默认追加 `serply`，留空 Key 时该后端直接跳过，不影响现有 Exa / You.com / mcporter 链路。
- **为平台数据撤回补齐事件来源归属**：`events` 新增 `source_platform`、`content_id` 和 `source_confidence` 三个持久化字段；新事件统一按显式来源 → metadata → 规范 URL → 兼容默认解析，无法确认的事件保持 `legacy_unknown`。旧库只在首次补列时按 metadata / 规范 URL 保守回填，旧 metadata 继续保留，未在本次变更中删除事件或实现撤回动作。
- **收紧事件来源兼容读取**：来源统计优先读取事件顶层归属；事件 identity 的稳定内容 ID 改由跨模块共享注册表提取，覆盖 X / 知乎等非 B 站事件；schema 版本提升至 6。
- **修复候选内容身份重复**：共享身份清单按优先级选择每条事件的第一个有效字段；例如同时存在 `content_id` 与 `topic_id` 时只保留规范 `content_id`，缺失时才使用备用字段，避免 seen ledger 为同一内容写入多个键。
- **AI 文案换行保留（issue #184）**：推荐理由、惊喜理由、探针理由等 AI 生成文案在插件 side panel、桌面 Web 与移动 Web 统一使用 `white-space: pre-wrap` 保留换行，不再把多行输出显示成一大坨；聊天回复仍沿用既有的安全 Markdown 渲染。
- **认知循环上下文预算可配置（issue #169）**：`[soul]` 新增 `awareness_event_batch_size`（默认 300，范围 10..900）、`insight_note_batch_size`（默认 150，范围 10..450）与 `cognition_max_tokens`（默认 32768，范围 1024..128000），分别对应 `cognition_cycle` 原有的 `_AWARENESS_EVENT_BATCH_SIZE` / `_INSIGHT_NOTE_BATCH_SIZE` / `_COGNITION_MAX_TOKENS` 三处模块常量。默认值不变；80-100K 上下文的本地模型（如 qwen3.8-27B）可在 config.toml 调小这些值，不必再改源码。三处 SoulEngine 构建面（CLI、OpenClaw bootstrap、API 热重载）与 `GET/PUT /api/config` 已同步透传，`docs/modules/config.md` 与 `docs/modules/soul.md` 已更新。
- **修复有 Key 无模型的 legacy provider 被投影为启用的空实例**：`effective_llm_instances` 不再把 `[llm.<provider>]` 中只填了 `api_key` 但 `model` 为空的固定 provider 块投影为 v2 实例（这些通常是历史模板残留），避免桌面 Web / API 保存时被 blocking「启用的 LLM 实例必须明确填写模型」拦下。默认 provider / fallback / 模块路由显式引用的 provider 仍会投影并由原生 v2 校验给出缺模型提示；每条空模型跳过打一次 WARNING。

## v0.3.208：来源周期回拉逐源开关与发布同步（2026-08-18）

- **修复 LLM 预算首次告警被系统运行时间误吞**：`_last_llm_budget_warned_at` 初值从 `0` 改为 `-inf`，避免在机器启动不足一个预算窗口时，第一次触发后台 LLM 预算暂停不输出 WARNING。
- **发布状态**：后端 / 插件 / 桌面安装包 / Docker 镜像与聚合 Release 均已发布为 `v0.3.208`，详情见 [Release](https://github.com/whiteguo233/OpenBiliClaw/releases)。Chrome Web Store 已上传并提交 `0.3.208` 审核；Firefox AMO 已提交 listed `0.3.208`。聚合 Latest Release 已附扩展 ZIP / Safari DMG 与四份桌面安装器。
- **国内下载入口**：README、GitHub Pages 首页、文档导航与 Gitee v0.3.208 发行说明均补充 123 云盘国内下载入口；当前分享永久有效并支持免登录下载超过 Gitee 100 MB 附件上限的 macOS / Windows 大安装包。
- **来源周期回拉支持逐源开关（issue #180 相关）**：`[sources.<slug>]` 新增 `incremental_enabled`（默认 `false`），与 `[scheduler].source_incremental_enabled` 总开关组成“总开关 + 每来源开关”两级控制；插件 side panel 与桌面 Web 的「平台源」配置页为 XHS / 抖音 / YouTube / 知乎 / Reddit / Linux.do / V2EX 增加“允许扩展周期回拉”勾选项，调度页暴露总开关。后端只有两级开关都为 `true` 时才入队周期 bootstrap 任务；关闭某来源时其 scheduler-owned pending / stale in-progress 任务会被取消，避免再打开前台标签页。默认所有开关均为关闭，手动初始化、`fetch-*` 与后台 discovery 不受影响。
- **后台 LLM 自设预算 + Embedding 熔断（issue #188）**：`[scheduler]` 新增 `llm_budget_max_calls`（默认 120 / 小时）与 `llm_budget_window_seconds`（默认 3600），daemon 通过共享 `LLMConcurrencyGate` 统计后台 LLM 请求，窗口内达到上限即暂停自动发现 / 补池 / 画像等循环并打一条 WARNING，避免无人值守时持续烧 DeepSeek 等付费额度；手动 CLI / API 请求不受影响，`0` 可关闭预算。`EmbeddingService` 新增连续失败熔断：默认连续 3 次异常或空向量后冷却 60 秒，冷却期内不再触碰不可达 embedding 端点、不再逐条打 full-traceback WARN，冷却后自动重探。
- **修复 defer(暂缓) 后假设仍显示在待聊确认（issue #189）**：`/api/chat/pending-confirmations` 现在读取 `memory/dialogue_confirmation_state.json` 中的 `deferred_until`，冷却结束前已 defer 的假设不再出现在待聊列表；冷却到期后自动恢复。用户主动 open 仍按原契约绕过冷却，不会因列表过滤而误 404。
- **修复停用知乎来源后扩展仍打开知乎任务标签页（issue #187）**：`/api/sources/zhihu/next-task` 现在在领取自动任务前动态检查 `[sources.zhihu].enabled`；来源关闭时返回 bodyless 204，scheduler-owned 增量任务会被清理避免卡住其它来源，手动 pending 任务保留、重新开启后可恢复。`SourceIncrementalSync` 的 active 任务复核同步忽略已停用来源，避免遗留知乎任务阻塞周期调度。
- **修复 `--llm-preset` 写入 `openai` 导致中转站 DeepSeek 推理 token 耗尽（issue #175）**：`agent_bootstrap` 的 OpenAI 兼容 preset（`relay` / `kimi` / `qwen` / ...）现在隐式写入 `provider_type="openai_compatible"`，使通用 OpenAI 兼容适配器能对中转站 DeepSeek 关闭 thinking；历史 `--provider openai` 与 preset 组合自动 remap，显式非兼容 provider 仍冲突报错。同步更新 `docs/agent-install.md` 的 preset 说明。
- **修复 bootstrap 二次运行追加重复 TOML 实例段（issue #174）**：`agent_bootstrap` 的 section 匹配现在忽略裸键/引号键差异，`[llm.instances.openai]` 与 `[llm.instances."openai"]` 视为同一表，避免 `tomllib` 报 `Cannot declare ... twice`。
- **补充 PR #182 贡献者致谢**：README 中英文与贡献指南正式记录 [@LHMQ878](https://github.com/LHMQ878) 对 `agent_bootstrap` 引号键 TOML 实例段 section 匹配修复的贡献。
- **修复非 DeepSeek 安装被默认空 Key 实例拦截（issue #176）**：`agent_bootstrap` 选择其他 LLM provider 时，会自动停用样例中的无 API Key DeepSeek 实例并从 `default_chain` 移除；已有 DeepSeek Key 的备选实例保持不变，`init` 不再要求用户手改 `config.toml`。
- **修复 Windows PowerShell 5.1 安装器在 clone 成功后静默退出（issue #177）**：`install.ps1` 现在在检查 `$LASTEXITCODE` 前捕获 `git clone` 的 stderr；PS 5.1 不再把 Git 的正常进度输出误判为终止错误，完整 clone 会继续运行 bootstrap，真实 clone 失败仍会显示 Git 原始诊断并清理临时日志。
- **修复桌面 Web 关闭自动续页后后台仍消耗可换库存（issue #81）**：已有推荐卡片时切回标签页、配置应用和状态水合不再请求可能触发首屏补池的 `/api/recommendations`，只同步 runtime / 库存状态；只有空列表首屏或用户明确手动刷新才读取推荐快照，已显示卡片和库存开关边界保持稳定。
- **修复 YouTube bootstrap 任务卡在 `in_progress` 无法回收（issue #178）**：Chrome MV3 service worker 休眠会丢失 `setTimeout`，导致扩展 `yt-task-dispatcher.ts` 的任务超时不再触发、`/api/sources/yt/next-task` 被一条 stale `in_progress` 任务长期占住。扩展侧改为 `chrome.alarms` 一次性定时器（`when: deadline_at`）做任务超时，并把 `{task_id, deadline_at, tab_id}` 写入 `chrome.storage.session`（不可用时回退 `chrome.storage.local`）；alarm 触发或 worker 重启后检测到持久化记录时回传 `task_timeout` / `service_worker_restart` 终态并关闭孤儿 tab。后端 `YtTaskQueue` 新增 `expire_stale_in_progress()`，在 `/api/sources/yt/next-task` 领取前与 `enqueue_yt_bootstrap` 入队前把超过阈值（`OPENBILICLAW_YT_STALE_IN_PROGRESS_SECONDS`，默认 600s）且无 staged canonical 结果的 `in_progress` 任务自动置 `failed`；带 `_openbiliclaw_terminal_status` 的结果保留不覆盖，仍走 stale-reclaim 修复投影；`next_pending` 同时把 `claimed_at IS NULL` 的旧 in-progress 行纳入可重领范围。
- **修复密码门禁下首次设置模型保存返回 403（issue #171）**：setup 向导的同源请求统一携带既有 `X-OBC-Auth: 1` CSRF 请求头与 Cookie，覆盖模型配置保存、模型发现和向量模型修复；后端 CSRF 策略保持不变。
- **修复 YouTube bootstrap DOM 选择器失效拉取 0 条（issue #173）**：YouTube 已将历史 / 喜欢 / 订阅页的卡片从 Polymer（`ytd-video-renderer` 等）迁移到 Lit（`yt-video-card-renderer` / `ytd-video-card-renderer` / `yt-lockup-view-model` / `ytd-reel-item-renderer`），旧选择器只命中 Shorts，普通视频全部漏抓。`extension/src/content/yt/task-executor.ts` 的选择器扩展到新旧组件，并增加「卡片内任意 `/watch` / `/shorts` 链接」兜底、标题 `aria-label` / `title` 兜底、Shorts URL 正确拼成 `/shorts/<id>`；频道卡片同样覆盖新旧组件并支持 `/@` 手柄链接。新增 `tests/yt-task-executor.test.ts` 锁定新旧布局提取与去重行为。
- **修复 YouTube 新版 `yt-lockup-view-model` 卡片标题 / 频道 / 封面为空**：真实 Chrome 端到端验证发现当前 YouTube 历史 / 喜欢页的 `yt-lockup-view-model` 卡片把标题、频道和封面放在 light DOM 的 `ytLockupMetadataViewModel*` / `ytContentMetadataViewModel*` 类名下，而不是旧的 `#video-title` / `#channel-name` / `img#img`；这会让任务从「0 条」变成「有 video_id 但标题为空」的低质量信号。`task-executor.ts` 优先匹配新版类名（`a.ytLockupMetadataViewModelTitle` / `yt-content-metadata-view-model span.ytAttributedStringHost` / `yt-thumbnail-view-model img`），并保留旧选择器与 shadow-root 递归兜底。新增对应单测，并用隔离后端 + 真实登录 Chrome 跑通 `fetch-youtube`：观看历史 91 / 订阅 9 / 点赞 3，共 103 条事件。
- **Codex OAuth 升级为可实际调用的 ChatGPT 订阅传输（issue #170）**：`auth_mode="codex_oauth"` 不再把 ChatGPT/Codex token 当作 OpenAI Platform API Key 发给 `api.openai.com/v1`（该路径因缺少 `api.responses.write` scope 必 401），而是新增 `CodexChatGPTProvider`，请求发往官方 Codex CLI 同款通道 `https://chatgpt.com/backend-api/codex/responses`（SSE Responses 流，`Authorization: Bearer <token>` + `chatgpt-account-id` 头）。`base_url` 校验同步收紧为只允许官方 Codex 域名，第三方中转与 Platform API 组合继续被阻止；401 时自动刷新 token 并重试一次，刷新后仍失败返回稳定可操作的鉴权错误。`openbiliclaw login codex --import` 导入后立即做一次真实 LLM 能力探测（新增 `--status --probe`），结果持久化到本地凭据文件，CLI 状态页与 `/api/health` 新增 `llm_registered` / `llm_callable` 字段以区分「Provider 已注册」与「默认模型链真实可调用」；桌面设置与 `/setup/` 向导把 Codex OAuth 标为实验性可选项，并隐藏 API Key 与 /models 发现入口。Codex 后端不支持 Platform API 模型（如 `gpt-5-nano` 会被 HTTP 400 拒绝），探测/调用默认使用 Codex 后端模型 `gpt-5.4`，模型留空时先从官方 `backend-api/codex/models` 目录自动发现；`max_output_tokens` 因该通道拒绝而不再发送。
- **自动打开的任务标签页默认静音（issue #163）**：扩展新增 `background/task-tab.ts` 的共享助手 `createTaskTab()`，B 站搜索兜底、小红书、抖音、知乎、Reddit、Linux.do、V2EX、微博、YouTube 及原生保存/E2E 新开标签页统一在创建后立即 `tabs.update(tabId, { muted: true })`，避免抖音等自动播放内容在电脑无人值守时突然出声。Chrome 的 `tabs.create` 不支持 `muted`，因此跨 Chrome / Firefox / Safari 统一采用「先 create、再 update 静音」；静音跨后续导航保持，用户可在标签栏手动取消，静音失败不阻断任务。复用用户已有标签页的路径保持原样。
- **新增 OrcaRouter 聚合 provider**：`provider_type="orcarouter"` 以 OpenAI 兼容协议接入 OrcaRouter（`https://api.orcarouter.ai/v1`），一个 Key 跑 150+ 模型；复用统一超时 / 重试 / 错误归一化 / JSON mode 与 per-call model 覆盖；网关会原样转发推理参数给上游路由、非推理模型会以 HTTP 400 拒绝，因此适配器不发送 `reasoning_effort` 与嵌套 `reasoning` 对象，推理模型使用自身默认档位。后端 registry / 配置、API `/api/config`、CLI 向导与 `agent_bootstrap`、桌面 Web 设置页、首次运行 `/setup/` 向导与扩展 popup 均已接入，`config.example.toml` 提供实例模板。

## v0.3.207：补货提速与搜索后端升级（2026-08-15）

- **发布状态**：后端 / 插件 / 桌面安装包与聚合 Release 均发布为 `v0.3.207`，详情见 [Release](https://github.com/whiteguo233/OpenBiliClaw/releases)。
- **修复 `extension-v*` Safari DMG 校验路径**：Safari 打包 step 的 `working-directory` 已是 `extension/`，校验路径去掉重复前缀，避免 DMG 已生成却被误报 missing 导致插件发版失败。
- **修复 B 站份额已满时全局库存跑不满**：`_build_refresh_plan` 在“池子低于目标、但 `_build_source_replenishment_plan()` 为空（B 站已达自身份额，缺额来自 V2EX / 微博 / YouTube 等不可用或节流的来源）”时不再直接返回空计划；只要 discovery candidate 管线里没有 `pending_eval/evaluating` 在途工作，就回落到按 `trending_refresh_minutes` / `explore_refresh_minutes` 巡航的 B 站周期计划，让健康超份额来源继续引入新 topic 补全局库存。份额再平衡仍会在欠份额来源恢复后把超份额行退坑，不破坏 pool-share fairness 的长期收敛。
- **减少来源侧的无效失败与告警**：`build_inspiration_search_provider` 在 `mcporter` CLI 缺失时直接跳过 Exa / You 后端并给出一次性可操作告警，V2EX 搜索不再先触发 `FileNotFoundError` 再回退官方有界搜索；微博 `search` / `hot` 分支遇到 `upstream_rejected` 时按“本分支空结果”处理，不再把整轮微博标记为 error / 触发 60 秒 outcome backoff，让其它分支继续执行。
- **Exa / You.com 改为 Python 直连 API（mcporter 降级为可选 fallback）**：`[discovery]` 新增 `exa_api_key` / `you_api_key`；填写后 `ExaInspirationProvider` / `YouInspirationProvider` 直接通过 `httpx` 调用 Exa `POST /search` 与 You.com `GET /search`（`trust_env=False`），不再依赖本机 `mcporter` Node CLI。未填写 API Key 时仍按原逻辑回退 mcporter（若已安装），两者都没有则跳过对应后端。`build_v2ex_external_search_provider`、API runtime 与 CLI 三处组装同步传入 API Key。
- **新增无 key 的 Bing RSS 免费搜索兜底**：`BingRssInspirationProvider` 调用 `bing.com/search?format=rss`（标准 RSS 解析，`trust_env=False`），默认后端链变为 `local_cache → platform_sources → bing_rss → exa → you`；即使没有 mcporter、也没有 Exa / You API Key，灵感 grounding 仍有真实全网搜索结果。V2EX 的 `site:v2ex.com/t` 外部召回链也接受 `bing_rss`，无 Exa/You Key 时先试 Bing RSS 再回退官方 latest/hot 有界搜索。
- **Safari Web Extension 支持（issue #156）**：`extension/` 新增 `manifest.safari.json` 与 `build:safari` / `clean:safari` / `verify:assets:safari` / `convert:safari` 脚本，产出自包含 `dist-safari/`，并由 `scripts/convert-safari.mjs` 调用 Apple `safari-web-extension-converter` 生成 Xcode 工程（仅 macOS + Xcode 可用）。Safari 不支持 `side_panel` / `sidePanel` / `notifications` / `world`（MAIN-world 内容脚本），因此 Safari manifest 改用 `action.default_popup` 承载同一套 popup UI，去掉上述权限与 `world` 字段并保留 `alarms` / `scripting` / `cookies` / `storage`；service worker 对 `chrome.notifications` / `chrome.alarms` 注册加空值守卫（无该 API 时降级为 WS 驱动刷新而不崩溃），Safari 构建额外经 esbuild banner 注入 `browser → chrome` 兼容 shim（在已提供 `chrome` 的环境为 no-op，不影响 Chrome/Firefox 产物）。**MAIN-world tap 恢复为 page-context 桥接**：`content/safari-page-injector.js` 在 `document_start` 按 hostname 把 B 站 / 小红书 / 抖音 / X / Bangumi 的 `main/*.js` tap 以 `<script src>` 注入页面上下文（已列入 `web_accessible_resources`），隔离世界既有 `window.postMessage` 监听不变，Safari 上的网络层确定性点赞/收藏/评论与登录态识别恢复生效（best-effort，页面 CSP 可能拦截）。**Cookie 同步加固**：`cookie-sync.ts` 改为全量读取 + JS 域过滤，规避 Safari `cookies.getAll({domain})` 的精确域差异，并在 unfiltered `getAll({})` 不可用时逐域回退。已验证 `npm run build:safari` → `convert:safari` → `xcodebuild`（macOS target）全链路构建成功，1402 条扩展测试与 `typecheck` 通过；构建/签名/已知限制矩阵见 `docs/safari-extension-build.md`。
- **Safari 插件接入 `extension-v*` 发版链（无需 Apple 凭据即可出包）**：`release-extension.yml` 新增 macOS 构建腿，推 `extension-v*` tag 时在 `macos-14` 上自动执行 `npm run package:safari` 打 `openbiliclaw-extension-v*-safari.dmg`，与 Chrome/Firefox 资产一起上传 `extension-v*` 与 `openbiliclaw-v*` 聚合 Release。CI 检测到 `APPLE_DEVELOPER_ID_CERTIFICATE_BASE64` / `APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD` / `APPLE_TEAM_ID` / `APPLE_NOTARY_USER` / `APPLE_NOTARY_PASSWORD` 齐全时自动切换 `npm run package:safari -- --notarize`（Developer ID 签名 + notarytool 公证 + stapler staple）；缺凭据或 `SAFARI_SIGNING_ENABLED=false` 时回退 ad-hoc 未签名 DMG（实验包，需用户在 Safari 开启「允许未签名扩展」），不再 fail-closed。`verify-release-completeness.yml` 始终把 Safari DMG 列为 `extension-v*` / 聚合 Release 完整性门禁，`sync-aggregate-release.sh` 同步展示 Safari 下载行并注明签名/未签名安装差异。新增 `extension/scripts/package-safari.mjs`：本地 `npm run package:safari` 即可产 ad-hoc DMG/ZIP，完全不需要 Apple 账号。

## v0.3.206：with-embedding 崩溃修复与可靠性提升（2026-08-15）

- **项目首页与 README 重新对齐**：补齐一直遗漏的 YouTube / X 来源卡，使首页明确展示 B 站、小红书、抖音、YouTube、X、知乎、Reddit、Linux.do、Bangumi、V2EX、微博与开放 Web；首屏补回“本地运行、只为一个人构建、反馈可调教”的定位，产品入口从过时的“只有浏览器侧边栏”更新为浏览器插件、桌面 Web、移动 Web、Flutter 与 DSH 五端，并修正中文微博文案误用英文、Firefox、架构分层、聚合 Release 说明以及静态 HTML / 中文词典漂移。
- **README 新增 Linux.do 友情链接（折叠）**：主项目 README（中英）顶部原有的「LINUX DO Community」徽章移除，改在 README 底部新增可折叠的「友情链接」区块，内含指向 https://linux.do/ 的 LINUX DO 友情徽章；讨论帖徽章保留。DSH 插件仓库（dsh-openbiliclaw）README 底部同步新增同款折叠友情链接。
- 修复 `scripts/install.ps1` 在原生 Windows 上的一键安装解析失败（issue #157）：双引号字符串内 `$InstallDir:` 会被解析为作用域限定变量引用，导致整个脚本在 PowerShell parse 阶段直接报错，改为 `${InstallDir}`；同时为脚本补充 UTF-8 BOM，确保 Windows PowerShell 5.1（脚本声明 `#requires -Version 5.1`）按 UTF-8 解码含中文注释与 here-string 的内容；`Invoke-Bootstrap` 内的 `$args` 改名 `$bootstrapArgs`，避免遮蔽自动变量（`PSAvoidAssignmentToAutomaticVariable`）。
- **修复 with-embedding 安装包 bge-m3 调用 500（llama-server `0xc0000005` 访问违规）的诊断与随包版本**：Windows 随包 Ollama 从 `0.30.6` 升到 `0.32.13`（`release-desktop.yml` / `build-installers.yml` / `docker/ollama-bundled.Dockerfile` 同步），降低旧版 llama-server 在 embedding 负载上的崩溃概率；`ollama_diagnostics` 新增 `0xc0000005` / access violation 识别，`model_broken` 文案改为按「一键重拉 → 重启 → 内存/虚拟内存 → 杀软白名单 → 升级安装包」排序排查，不再只说「下载不完整或内存不足」；托管 Ollama 在桌面包启动时（`OPENBILICLAW_PROJECT_ROOT` 已设置）把 `ollama serve` 与 llama-server 的 stdout/stderr 写入 `<project>/logs/ollama-managed.log`，让后续同类崩溃有原生日志可查，CLI / dev / 测试仍保持 DEVNULL。测试补充访问违规分类与日志捕获 / 关闭。


## v0.3.205：证据驱动时效推荐与可靠性升级（2026-08-14）

- **发布与市场状态**：`backend-v0.3.205`、`extension-v0.3.205`、`desktop-v0.3.205` 与聚合 `openbiliclaw-v0.3.205` 均指向提交 `a49af312`；聚合 Latest Release 已附两份扩展 ZIP 与四份桌面安装器。Chrome Web Store 已上传 `0.3.205` 并进入 `PENDING_REVIEW`；Firefox AMO 已接受 listed `0.3.205`，文件状态为 `unreviewed`。AMO `eula_policy` API 仍返回 HTTP 406，manifest 数据类别、reviewer notes、商店描述和随包隐私政策已提交。
- **新增 DeepSeek Harness（DSH）客户端插件（独立仓库）**：OpenBiliClaw-dsh 插件把消费侧搬进 DSH Web GUI——DSH 界面常驻第四栏（aside 槽位），提供推荐 / 内容库 / 对话 / 画像 / 设置面板，并注册 22 个 `openbiliclaw_*` Agent Bridge 工具与 `openbiliclaw-adapter` skill，让 DSH 里的 Agent 也能读推荐、答探测、保存内容、参与学习闭环。插件只做消费侧，爬取 / 平台源管理 / 账号同步仍留在主项目。README（中英）顶部新增「重要更新」提示，核心入口由四个更新为五个（浏览器插件 / 桌面 Web / 移动 Web / Flutter 原生客户端 / DSH 插件）。
- **修复 embedding L2 缓存无界增长（issue #153）**：`data/embedding_cache.db` 持久化向量从 JSON 文本改为版本化 little-endian float32 BLOB（`OBLV` 头 + dtype/dimension 元数据），4096 维向量从约 90 KiB/行降到约 16 KiB/行；读取对 legacy JSON、BLOB 与降级回写产生的 mixed-format 数据按内容自适应解码，单行损坏只降级为 miss。旧库启动时自动执行幂等、小批量、可中断续跑的 JSON→BLOB 迁移（进度即 `encoding` 列），schema 升级补齐 `dimension` / `created_at` / `last_accessed_at` 元数据。新增可配置磁盘预算 `[llm.embedding].cache_max_bytes`（默认 0 = 不设上限）与高低水位，超高位后按「非 active namespace（含 legacy 行）→ active namespace 最旧/最近未访问」顺序批量淘汰；`last_accessed_at` 命中刷新限频，写路径按节奏抽样检查。新增 CLI `embedding-cache-stats`（行数 / 逻辑载荷 / 文件与 WAL 大小 / namespace 分布 / 水位 / 最近维护）与 `embedding-cache-clean`（默认 dry-run，`--apply` 执行迁移 + 回收失效 namespace + WAL checkpoint / `VACUUM INTO` / `integrity_check` / 原子替换做物理回收，`--keep-model` / `--keep-legacy` / `--no-compact` / `--batch-size`）。四表面契约：CLI 与后端程序化接口覆盖；桌面 Web / 插件 / 移动 Web 暂未提供该维护入口（属运维面，PR 显式声明排除）。
- **README Star 历史图表临时替换为 Star 徽章**：GitHub 自 2026-06-30 起将 stargazers API 限制为仅仓库管理员/协作者可读，star-history 实时图表对非协作者全部渲染为「GitHub restricted access to star data」错误占位图；README（中英）暂以 shields.io Star 徽章替代并保留说明，待上游恢复或配置新的加密 token 后换回实时图表。
- **新增「重新初始化 / 重建画像」入口（gui-init §4 收口）**：已初始化后，桌面 Web 设置页「通用 → 初始化与画像」与扩展 popup 通用 tab 提供「重新初始化 / 重建画像」按钮（`window.confirm` 二次确认后调 `POST /api/init {force:true}`，成功后回推荐 tab 展示四阶段进度）；CLI `openbiliclaw init --force` 跳过已初始化二次确认并按重新初始化执行（交互终端默认检测到已初始化时先 y/N 确认，非交互保持直接重跑）。语义为 force 重建：重新拉取所选平台数据、重建完整画像并补足首轮发现池，现有事件 / 收藏 / 对话历史 / 手动编辑覆盖全部保留。**force 重初始化同时清空旧推荐池**（`pool_status='purged_by_reinit'`，按新画像重新发现并生成首轮推荐，避免旧画像推荐滞留并顶满 backfill 目标）；可选 `reset_cognition`（CLI `--reset-cognition` / API body / 设置页复选框）清空旧 awareness / insight 认知层，适合换账号或大改兴趣。**重初始化前自动创建快照备份**（`data/backups/reinit-<时间戳>/`：SQLite 冷备 + `data/memory/` 全部画像/认知层，CLI 可 `--no-backup` 跳过），重建覆盖的画像与删除的认知层均可恢复。后端 `POST /api/init` 的 `force:true` 绕过 `already_initialized` 守卫，其余前置复验与写者门控不变；移动 Web 无设置页，重新初始化入口按四表面契约声明排除。
- **时效资格升级为证据驱动三态生命周期（temporal v2）**：Evaluation Agent 继续把相关性与时效解耦，并原子输出 `class/confidence/reason + validity_mode/valid_until/scope/evidence/state`；其中 evidence 必须是 Agent 实际可见 prompt projection 的逐字摘录，state 明确区分 `unknown/active/expired/superseded`，`evaluated_at/next_review_at/policy_version/evidence_complete` 则由代码生成并在 storage 重算。确定性 policy 返回 `eligible/review_due/expired`：只有置信度 `>=0.80`、证据组完整、`scope=core` 且 grounding 成功时，已过明确 deadline 或事件 `expired` / 版本 `superseded` 才 hard expire；deadline 证据必须明确给出日期、具体时刻和时区并与 `valid_until` 为同一瞬间，终态证据必须正向明示结束 / 替代，`active` 证据也必须正向明示仍在进行 / 仍受支持，条件句或泛化复核提示不能解除 hold。日期-only、反向证据、标题钩子、低置信、缺失、malformed、未 grounding 与不一致组合全部 fail-neutral；`freshness_only` 也必须逐字 grounding，虚构摘录不会生成复审时钟。`breaking/current/versioned` 的 1 / 14 / 120 天仅为复审节奏，旧 v1 `breaking/current` 的 3 / 60 天也只触发复审，不再按年龄判死。`review_due` 的 discovery 行回到 `pending_eval`，正式池行进入可逆 `pool_status='temporal_review_hold'`，不可展示、不计库存并复用现有评估链；两边失败复审均按逐行 1 / 2 / 4 / 8 / 16 / 24 小时领取有界租约，候选租约未到时不 claim、也不计 raw / projected / 来源容量；若模型执行超过租约，完成、失败与 orphan/release 出口会从落库时续租，避免立即热循环。只有高置信 grounded core 证据或高置信 `evergreen/historical + mode=none` 耐久结论能覆盖旧强分类并解除 hold；低置信、hook-only、中性或未 grounding 复审一律保留旧证据和租约，确定过期才进入 `rejected_temporal_stale` / `stale`。SQLite 在写锁内把整组证据与 lifecycle outcome 原子保存，raw 重抓和旧缓存也不能局部洗字段或复活 hold/stale；pipeline 在 admission 前重读 durable row，只有数据库最终状态仍为 `evaluated` 才可写入正式池。等待扫描、canonical 读取、copy/delight/通知、`PoolServeSnapshot`、最终 recommendation + shown 写事务和 API 1 秒 single-flight 快照使用同一 policy 复核，连续刷新与 snapshot 竞态不能绕过。publication bonus 与 ranking shadow 保持独立：前者只在合格内容之间排序，后者继续只保存聚合观测。
- **新增 Flutter 原生移动客户端（独立仓库）**：OpenBiliClaw-mobile 提供 Android / iOS / Web / Linux / macOS / Windows 全平台客户端，连接同一本地后端；推荐 / 对话 / 画像 / 收藏与 30 天历史 / 消息收件箱齐全，B 站封面 CDN 直连省两跳。首个安装包已随其 Latest Release 发布（Android 签名 APK / iOS 未签名 IPA）。README（中英）、项目主页与文档导航同步加入入口链接与下载入口。

## v0.3.204：后台来源调度与搜索修复（2026-08-11）

- **发布与市场状态**：`backend-v0.3.204`、`extension-v0.3.204`、`desktop-v0.3.204` 与聚合 `openbiliclaw-v0.3.204` 均指向提交 `72a92a96`；聚合 Latest Release 已附两份扩展 ZIP 与四份桌面安装器。Chrome Web Store 已上传 `0.3.204` 并进入 `PENDING_REVIEW`；Firefox AMO 已接受 listed `0.3.204`，文件状态为 `unreviewed`。AMO `eula_policy` API 仍返回 HTTP 406，manifest 数据类别、reviewer notes、商店描述和随包隐私政策已提交。
- **修复 V2EX 三种关键词模式的 Search 空召回**：正式 V2EX Search 不再被 inspiration 关键词开关误关；Exa / You 不可用时，匿名 latest/hot fallback 对 planner 多段长词采用整句精确优先、非通用核心词受限放宽，并继续交给共享 evaluator / admission。`keyword-inspiration-preview --platform v2ex` 现可用且在来源启用时注入只读 V2EX grounding client；同步修正 CLI 平台帮助与模块文档。
- **Linux.do 后台任务瞬时 listener 竞态修复**：同源 task tab 在 Discourse challenge / SPA 初始化期间发送消息失败时，扩展会在同一 task ID 上短间隔重试，并最多重载一次原 runner tab；只有有界恢复失败才回传 `sendMessage_failed`，避免闹钟调度把瞬时 content-script 缺失误判为任务失败后连续制造重复任务。Linux.do CLI 预览同时按 `linuxdo-*` strategy 过滤，避免显示共享候选管线中其它来源的旧条目。
- **所有扩展账号周期回拉改为默认关闭**：新增 `scheduler.source_incremental_enabled=false` 安全总开关；旧配置即使保留 `source_incremental_hours=24`，未显式 opt-in 也不会检查扩展 presence、创建账号 bootstrap 任务或打开平台标签页。scheduler-owned 任务带独立标记，升级前由调度状态记录的残留任务也会在领取前终止，避免知乎、Reddit、Linux.do 等来源在用户浏览其它页面时切换标签页并抢走焦点；手动增量任务不受误伤。手动初始化、手动 `fetch-*` 和正常 discovery 保持不变；设为 `true` 后才恢复原有全局 / 逐源周期。

## v0.3.203：微博登录态初始化与插件可用性修复（2026-08-11）

- **发布与市场状态**：`backend-v0.3.203`、`extension-v0.3.203`、`desktop-v0.3.203` 与聚合 `openbiliclaw-v0.3.203` 均指向提交 `cf5ac276`；聚合 Latest Release 已附两份扩展 ZIP 与四份桌面安装器。Chrome Web Store 已上传 `0.3.203` 并进入 `PENDING_REVIEW`；Firefox AMO 已接受 listed `0.3.203`，文件状态为 `unreviewed`，当前聚合包提供 Firefox 临时加载 ZIP。AMO `eula_policy` API 仍返回 HTTP 406，manifest 数据类别、reviewer notes、商店描述和随包隐私政策已提交。
- **微博补齐登录态初始化**：微博从 discovery-only 升级为 capability-specific full source；`init --yes-weibo` / `/api/init` 会在扩展确认浏览器登录和当前 uid 后，通过隔离同源任务只读导入收藏、关注和 mentions，后端只保存布尔 heartbeat、账号绑定与规范化事件，不接收 Cookie。个人 bootstrap 暂为 init-only，公开 discovery 仍匿名可用。
- **修复微博 H5 登录态 bootstrap 路由**：适配当前移动端接口 `/api/container/getIndex?containerid=230259`、`/api/friendships/friends`、`/message/mentionsAt` 与 `/message/mentionsCmt`，旧路径仅作兼容回退；HTTP 404 不再被当作真实空结果，扩展构建与任务回归测试同步更新。
- **修复桌面 Web 配置页空白**：合并 Linux.do / V2EX 来源卡片时出现的嵌套 HTML 让后续设置面板被错误收进来源卡片；现将两张来源卡恢复为同级节点，并加入 DOM 结构回归测试，确保模型、平台源、调度和高级设置面板都能正常渲染。
- **抖音账号周期回拉改为默认关闭**：`douyin_incremental_hours` 的缺省值从继承全局 24 小时改为 `0`，避免作品 / 收藏 / 点赞 / 关注的 `bootstrap_profile` 任务定期打开前台抖音页并抢走用户焦点；需要该能力时可显式设置 `1..168` 小时开启，手动初始化、`fetch-douyin` 和后台 feed / search / hot discovery 保持不变。
- **恢复插件底部「最近发生的事」动态栏**：side panel 的全局活动栏此前在切到「对话」Tab 时被 CSS 整块隐藏，看起来像功能消失；现改为四个一级 Tab 始终可见，聊天记录继续在剩余空间内独立滚动，输入框仍固定在聊天区底部，并加入回归测试防止再次按 Tab 隐藏。
- **收紧插件覆盖层与短侧栏可用性**：设置、消息和手机二维码覆盖层现在使用 modal 语义、背景 `inert`、Tab 焦点圈定、Esc 关闭与触发点焦点恢复，不再让键盘焦点落到被遮住的主页面或动态栏；动态历史高度随 `dvh` 收缩，避免矮侧栏展开后把底部挤出视口；停用来源的卡面也会退出键盘顺序并标记 `aria-disabled`，但启用开关仍可操作。
- **修复扩展配置页空白**：Linux.do 来源卡片缺少两个闭合标签，导致通用、调度、高级功能和日志面板被浏览器嵌入隐藏的平台源面板；现已恢复为同级面板，并加入 HTML 结构回归测试。

## v0.3.202：Linux.do、V2EX 与微博来源扩展（2026-08-10）

- **发布与市场状态**：`backend-v0.3.202`、`extension-v0.3.202`、`desktop-v0.3.202` 与聚合 `openbiliclaw-v0.3.202` 均指向同一绿提交；聚合 Latest Release 只包含两份扩展包和四份桌面安装器共六个 `0.3.202` 资产。Chrome Web Store 已上传并进入 `PENDING_REVIEW`，Firefox AMO 已接受 listed `0.3.202`，文件状态为 `unreviewed`。AMO 隐私字段 API 仍返回 406，manifest data-collection 声明、reviewer notes、商店描述和包内隐私政策已随提交提供。
- **新增 Linux.do 只读来源接入**：后端新增 `linuxdo_tasks` durable 队列、`LinuxdoDiscoveryProducer`、统一 topic/event 归一化、capability-specific source-auth 与周期增量同步；公开 discover 匿名可用，个人 profile/bootstrap/incremental 必须由同源会话正面确认。CLI 新增 `fetch-linuxdo` / `discover-linuxdo`，通用 `discover --source linuxdo` 也走同一 producer。五种 discovery 分支为 search / hot / feed / creator / related，三种个人初始化 scope 为 bookmarks / likes / read history。
- **扩展采用同源、最小回传边界**：Linux.do 请求只在真实 `linux.do` task tab 内以 `GET` + `credentials: include` 访问 JSON endpoint；Cookie `_t` 只转换成登录布尔心跳，Cookie 值、CSRF 字段和未裁剪原始响应都不上传。任务具备 tab/task 隔离、分页与条数上限、超时、2 MiB 响应上限及结构化错误。
- **真实安装版端到端验证完成（修复后仍保留重跑门禁）**：2026-08-09 在已登录 Linux.do 的 Chrome unpacked extension 上完成热更新与真实只读链路。bootstrap 两轮均返回 bookmarks=2、likes=5、read history=100（共 107 条），第二轮 durable ingress 零重复；search / hot / feed / creator / related 五分支均取得 canonical topic，五种正式 producer 均完成真实模型评估，组合运行继续遵守全局 limit 与候选幂等。测试还在真实 `in_progress` 任务中触发完整扩展重载，复现并修复 MV3 runner 丢失后以同一 task ID 安全恢复的问题。任务结果字段白名单、canonical ID/URL、first-final-wins 与无 Cookie/token/raw-response 均通过数据库断言。旧 `suggested_topics` 可能合法为空且语义是 new/unread/random 站点建议，不是严格相关；related 已改为 topic detail + 官方 `/topics/similar_to.json`，最终安装产物仍需补跑真实 Chrome/Firefox 门禁。
- **Linux.do 契约审计补漏**：新增 capability-specific auth matrix、账号 key fail-closed 分区、跨扩展实例 single-flight + claim token、backend-owned scope/cap/关键词 ID/交互 action 校验、严格 JSON Content-Type 与 true-empty 证据、partial/failed first-final staged replay、retained-only 日预算、持久分页 cursor、failed/degraded 不推进增量 cadence、guided-init success 时间种子、所有 claimed final 的 durable ACK/MV3 outbox、前台 tab 恢复和平台中性的缺作者文案。冻结 contract、acceptance ledger、自动审计器和 Chrome/Firefox build asset verifier 同步纳入发布门禁。
- **任务 tab 不再污染被动画像**：真实 E2E 发现 Discourse 会在 `document_idle` 前清掉 hash marker，自动任务页因而被误识别为普通浏览并写入 snapshot。任务入口现改用稳定 query marker，并继续兼容旧 hash 的恢复识别；后台任务页只运行只读 executor，不启动行为 collector。
- **长任务、重启恢复与部分成功语义**：Linux.do 默认端到端总等待为 32.5 分钟（pending 领取最多约 3 分钟、合法大任务按形状执行约 29 分钟、结果余量 30 秒），显式 CLI/env 等待值是总硬上限；claim lease 为约 35 分钟、共享 mutex stale 窗口为约 36 分钟。真实 `in_progress` 热重载复现了仅靠 `storage.session` 重绑会永久等待的缺口；runner 现把无凭据 task/tab/deadline 临时写入 `storage.local`，恢复后重发同一 task ID，仍存活的 content context 会合并重复执行，完整重载则安全重放只读 GET。bootstrap 部分 scope 或 discovery 分页 / 多输入中途失败以 `degraded` 保留有效 items，bootstrap `failed/degraded` 均不进入 6 小时近期任务复用；guided init 使用默认 Stage-1 预算时，Linux.do-only 至少给 32.5 分钟，多来源并选 Linux.do 时给 62.5 分钟，显式 override 不扩。
- **Linux.do MV3 恢复与 engagement 分支缺口修复**：runtime stream 现在先于可能等待 mutex 的恢复 barrier 建连；`storage.session` generation 区分普通 worker recycle 与完整 extension reload，完整 reload 会刷新 runner-owned 页面再重放同一只读 task ID。真实 Chrome 中两页 feed 在 `in_progress` 时热重载，约 25 秒后以同一任务行 terminal=ok，返回 37 个唯一 topic；修复全局 result cap 后的隔离复跑再次以同一任务行返回 36/36 唯一 topic。正式 search/related 同时增加 retained-only topic detail hydrate，真站各 2/2 候选均取得主题作者、浏览、总赞和回复，不再缺字段，也不使用匹配回复的作者/点赞冒充主题指标；双关键词 `max_items=1` 真站任务也严格只回传 1 条。两轮个人 bootstrap 各返回 `2 bookmark / 1 like / 20 read`，durable ingress 保持 23 条不重复；真实 404 被分类为 `failed/linuxdo_http_error` 而不是假空。扩展全量 1323/1323、Chrome/Firefox build 与 17/17 资产校验通过；冻结 Firefox 安装版真账号门禁仍单列未执行。
- **来源状态保留最近任务真值**：Linux.do 浏览器心跳优先；无心跳时，`/api/sources/status` 只读最近 `linuxdo_tasks` 作 `task_history` 间接证据。个人 bootstrap 成功可证明可选会话通路，公开 discovery 成功仍保持 `credential=none`；`login_required`、限流与运行中状态分别表达，不把匿名任务伪造成已登录。
- **新增微博匿名公开 discovery 来源**：后端以仅存内存的匿名 visitor 会话执行 search / hot / creator 只读发现，不读取用户 Cookie、不进入 guided init、不增加扩展 host permission，并复用统一候选评估、来源占比与三端文字卡。
- **重构新增平台来源 skill 为证据驱动的阶段门**：复盘本地 Codex session、Git/GitHub 首次接入与后续修复，把 `full / discovery-only / capability-increment / audit-only`、机器可读来源契约、逐能力 hybrid auth、fail-closed E2E 写动作边界、中央注册 audit、required/N/A 与 PASS/FAIL 分离、原子任务准入、MV3 恢复、真假空结果、增量同步、scope completeness、时间语义、双浏览器资产和安装包真机 provenance 固化为完成条件；新增历史失败索引与 skill 镜像/契约审计测试，只有全部 required gate 有证据通过才允许报告 complete，发布 mutation 仍需明确授权。独立盲测还复现并修复了 `browser_heartbeat` 对未知来源默认落到知乎的错源分派，现由 source→prefix 显式 registry 驱动，未知来源 fail closed，新增来源必须成组提供 DB getter、扩展 event handler 与往返测试。
- **V2EX 已安装扩展真实登录 E2E 通过**：在 `8420` 真实后端热更新开发扩展后，四个只读 scope 于 13 秒内返回发布 4、讨论 Topic 19、收藏主题 1、收藏 Node 0，并转换为 24 条 canonical 事件；登录态、observed identity、稳定 Topic ID / URL、事件 source 与 satisfaction 语义、四 scope 完整证据全部通过。`smoke_only` 未向真实库写入任何 V2EX event、Node Affinity 或收藏快照；隔离库重复写验证为首次 24/0、第二次 0/24。
- **V2EX 最终构建与 8420 热更新复验**：最终 Chrome 构建通过后端 runtime event 热重载并重新连回真实登录态，四 scope 再次返回 4 / 19 / 1 / 0、24 条 canonical 事件；真实库的 event、seen、Node Affinity 和收藏快照增量均为 0。五路公开读取为 Search / Tab / Hot / Latest 各 3 条、Node 5 条；隔离正式 Node producer 使用用户现有 LLM / Embedding 配置完成发现 3、入池 3、评估 3、准入 1、低分拒绝 2，只记录 1 条脱敏 usage，临时库自动删除。
- **修复 V2EX gzip 响应二次解码**：有界流读取拿到的是 httpx 已解码字节，旧实现却把上游 `Content-Encoding: gzip` 原样复制到新响应，真实 Node 请求会再次解码并报 `incorrect header check`；现在在保留解码后字节上限的同时剥离内容编码、内容长度和传输编码头，并新增 gzip JSON 回归测试。
- **V2EX 无封面卡收敛为紧凑文字卡**：桌面、移动 Web 和 popup 不再给 Topic 文字卡保留大块 16:9 空媒体区，改为有界正文预览、来源 / Node badge、作者 / 时间 / 回复数和原动作区；Chrome 商店三端截图已按最终构建重制。
- **修复真实 V2EX 末页越界重复翻页**：V2EX 对 `?p=3` 可能保持地址不变却渲染末页 `p=2`，executor 现读取 `.page_current` 与后续页链接作为权威耗尽证据；修复后同一真实任务从约 4 分半缩短到 13 秒，四个 scope 均 `complete=true`。
- **真实公开发现与正式评估链复验**：Search 真实返回 1 条，Node / Tab / Hot / Latest 各返回 3 条，分支 smoke 均为 0 本地写入 / 0 LLM；随后用用户现有 LLM / Embedding 配置在隔离库运行正式 producer → evaluator → admission，召回 4、入待评估池 4、准入缓存 3，主推荐池未被测试污染。
- **V2EX bootstrap 与画像链落地**：新增不污染画像的 `fetch-v2ex` smoke、四 scope staged task bridge、按 Topic 聚合的 `discussion_reply`、账号分区 Node Affinity、双完整快照 retraction/restore outbox，以及 PAT verified → browser observed → config/accepted 的后端身份阶梯；明确 PAT / 浏览器证据 freshness、身份冲突门禁和零站内写操作。
- **按真实 V2EX 页面和 API 收紧解析**：回复只绑定相邻 `.dock_area` / `.inner` metadata，API 2.0 按 `success/result` 解包，兼容旧 Topic 列表、Atom 嵌套作者/content/entry id 与 epoch rate-limit reset；producer 有界补 Topic 详情，并仅在 PAT 可用时读取 Reply 第一页生成讨论摘要。
- **V2EX full 来源硬化**：补齐逐能力鉴权 readiness、扩展 claim 前准入与 MV3 durable recovery、2xx result ACK、affirmative-empty / hidden / challenge 状态、配置驱动 scope cap、国内直连网络边界、最终保留候选预算和来源发布材料；桌面 / popup 身份冲突可交互选择，新账号证据先暂存、完整 Soul 构建提交后才激活，旧账号事件 / Affinity / 收藏快照保持隔离。被动点击不再从“回复 / 收藏”等按钮文字猜测站内成功动作；Topic 可见阅读满 30 秒后，Node / Topic / 域名 / active identity 校验通过才按 distinct Topic 幂等计入首版时间衰减 Node Affinity。V2EX 配置保存统一限制未知字段、数值与 slug，商店 / AMO 文案明确 A2 只检查存在性、零 Cookie 值与零站内写操作。

## v0.3.201：探针聊天与 dislike 即时推荐（2026-08-08）

- **修复“抓了一天但一条新可换库存也没补进”的双协调器死区**：8 月 9 日用户日志显示来源抓取实际完成 78 次 enqueue（输入 2,158、保留 740），候选评估完成 1,337 条，可换库存始终为 221–224；但 65–78 条 admitted 素材长期卡在待文案，`recommendation.write_expression` 自 8 月 7 日晚后为零。根因是 copy coordinator 在 unrestricted `copy_ready>=90` 时认为无需工作，而 candidate coordinator 又把全部同 topic 深层 `admitted_pending_copy` 算进 projected，`available + pending≈300` 后也停止评估。storage 现新增 canonical `admitted_pending_available`，只统计补齐文案后能进入 topic 三条展示窗口的 pending；公开加载与计数统一先剔除 durable seen / 不可链接行再套 topic cap，避免这些高分行占窗并让 eligible 产生假净增。评估的 projected/admission 改用该子集，表达需求改为 copy 水位缺口与 eligible 公开库存缺口的较大值并 eligible-first 领取，API/CLI/OpenClaw 三个组合根统一注入公开库存目标，`copy_ready_target_count=0` 仍保留 legacy drain-all。插件若首次 runtime HTTP 失败，后续 `pool_status` stream 会恢复 initialized 状态；三端同时把待整理素材与真实可换数分开显示，并把误导性的“上次成功补货/最近补进”改为“补货进展”。
- **V2EX 公开 discovery 首阶段接入**：新增只读 V2EX Client / Topic normalizer / producer，覆盖匿名 API、JSON/RSS Feed、可选 API 2.0 PAT 与 live probe，以及 `search / node / tab / hot / latest` 五个分支；候选通过共享关键词规划、inspiration grounding、评估和平台份额进入统一待评估池。配置、source-auth、CLI、runtime、桌面 Web、移动 Web / popup 文字卡和文档同步接入。
- **修复抖音 discovery 长时间空转与零结果误报**：本机任务历史复现到 `stale_pending` 分钟级循环、`tab_create_failed`、构建产物缺失，以及 feed 首屏响应早于任务 collector 后仍写 `ok + 0` 的竞态。Chrome / Firefox 构建现隔离清理并校验 manifest 资产；Douyin MAIN-world fetch / XHR tap 从 `document_start` 安装，页面替换网络原语后会幂等重包，isolated world 对早到的归一化 discovery 条目做 120 秒 / 256 条有界、按 scope 一次性回放，并单独记录 response observation。真实 `/jingxuan` 卡片不再只按 `a[href]` 解析，同时识别 `div[data-aweme-id]`、非 anchor `href` 与 `video_<id>`，网络没有续请求时也能从已渲染卡片提取 ID / URL / 标题 / 作者；完全未观察到 feed 响应且 DOM 也无内容时才触发原后台 tab 一次 bypass-cache reload，仍失败才返回 `feed_no_observed_response`，不会冒充真实空或对限流 / 风控重试。search dispatcher 会在提交前监听结果路由；搜索触发真实整页导航时，新文档只恢复采集阶段，同文档 SPA 则由 execution key 去重，并移除首次 ready 后重复导航首页的竞态。dispatcher 在具备 tab 能力且拿到跨来源 mutex 后才 claim，alarm / WS poll 单飞，task-result 要求 2xx 并做有界幂等重试；daemon 在扩展离线时前置跳过，并对基础设施失败 / 预算耗尽分别使用 15 / 60 分钟退避。
- **抖音任务领取增加跨扩展实例单飞**：扩展内存 mutex 只能覆盖一个 service worker；后端 `DyTaskQueue.next_pending()` 现在会在原子领取事务内检查全表未过期 `in_progress` lease，存在时不发第二条任务，挡住两个 unpacked 扩展 ID 或 Chrome profile 并发 claim。15 分钟过期 lease 仍按原协议优先重领并修复 staged result。
- **候选评估新增语义时效分型与近期供给 shadow 闭环**：Evaluation Agent 在保持 `relevance_score` 只表达画像相关性的同时，输出 `breaking/current/versioned/evergreen/historical/unknown`、置信度与理由；结果贯穿待评估池和正式缓存。PoolCurator 不再把 `discovered_at` 当内容发布时间，而只对高置信、发布时间明确的前三类发放有界正向 bonus，缺时间和常青/历史内容保持中性。基于历史候选池/发现池回放，B 站主 API 与扩展 fallback 各自只保留一个 1×5 的 `pubdate` recent lane，仍走统一相关性与 admission；推荐侧同步记录含 bonus 与 no-bonus Top10/50/100 的 aggregate-only class/source/age shadow，30 天 / 5,000 行有界留存，不含候选身份且不改变 serving。
- **Agent Bridge 与当前内核能力对齐**：OpenClaw 历史接入升级为 `agent-bridge/v2` 协议中立适配层，Hermes / WorkBuddy 可复用同一套能力协商、24 个 skill descriptor、JSON CLI 和 Python alias；补齐多源推荐、换一批 / 追加、活动流与平台可用性、四态兴趣 / 避雷探针、惊喜反馈、durable chat history、画像编辑、本地保存与显式授权 native-save 同步。后续新增对外能力必须同时登记 operation DTO、skill descriptor、CLI（适用时）、capability manifest 与集成文档，并配套 idempotency / state-changing 边界测试。
- **全面真实 E2E 后收紧多源与控制面可靠性**：source-scoped discovery 的历史回填先按策略平台过滤，B 站空结果不再混入 Reddit 缓存；抖音插件 search/hot/feed 共用一次等待预算，超时/取消任务落失败终态，daemon 在扩展缺席时零入队并对空跑、错误和预算耗尽退避；抖音 live probe 的 legacy 与正交状态保持一致。配置页 LLM 探测扩到有限 120 秒后端 / 125 秒客户端窗口，覆盖 Ollama 冷启动；桌面 runtime 看板不再显示 `dy_task_available` 原始控制帧。
- **桌面配置页支持整套可移植数据导出 / 导入**：本机打开 `/web`，可把磁盘 `config.toml` / `config.local.toml` 合并、移除整段 `[api.auth]` 后的单份可移植配置，以及 SQLite、画像与记忆文件、平台 Cookie 文件、图片缓存和少量安全桌面 UI 偏好导出为 `.obcbackup`；导入前会再次明确提示目标机器的当前配置和用户数据将被替换。
- **迁移包是带校验清单的明文敏感 ZIP**：每个成员记录 SHA-256 与大小，导入会限制压缩包 / 解压大小、文件数量、单文件和路径，拒绝路径穿越、符号链接、加密条目、格式版本不匹配、校验和不一致、无效配置和损坏 SQLite。迁移包可能含模型 / 来源 API Key、平台 Cookie、画像与历史记录，不提供虚构的“已加密”保证；整段 API auth（密码 / hash、session secret、设备 key 等）、日志、历史备份、embedding 缓存、评测 / 临时缓存、证书、自启动文件、OpenBiliClaw Web / 扩展访问会话、外部 CLI 凭据和环境变量值不导出。
- **导入先暂存、重启后再替换**：`POST /api/migration/import` 在运行进程中只完成上传、完整校验和私有暂存；下一次受支持的后端启动取得 migration runtime lock 后，才以 journal + 同目录替换原子应用配置、SQLite 与画像，失败会恢复原状态，成功后原 `config.toml`、`config.local.toml` 和数据目录按存在情况保留为 `pre-import-*.bak` 回滚副本。桌面端也不在 202 暂存时提前切换偏好，只在后端报告 `applied` 后按 `migration_id` 为每个浏览器应用一次白名单 UI 设置；之后用户修改不会被同一旧 status 重写。
- **跨机器但不复制机器身份**：导入保留目标机器的数据路径、数据库路径、API host / port、日志路径、网络 / TLS / 自启动设置、浏览器 CDP 地址，以及 Bilibili 专用代理和本机浏览器可执行文件路径；目标机证书与自启动文件也继续保留。整段 `api.auth` 以目标机现值为基线，再轮换文件 session secret、把 prepared DB 的 `auth_epoch` 严格提升为 `max(来源 epoch, 目标当前 epoch) + 1`、清空并关闭扩展远程访问；因此保留目标机门禁 / 密码 / proxy / Origin 策略，但即使 session secret 由环境固定，来源 / 目标旧 Web 会话仍失效且扩展设备需重新配对。
- **来源遗漏与目标覆盖分别可见**：manifest / 导入响应用 `source_omitted_environment_variables` 提示源机依赖但没有写入包的变量名（`OPENBILICLAW_*`、Gemini 标准 Key、系统代理 / CA）；暂存响应 / 状态另用 `target_active_environment_variables` 提示目标进程当前仍生效、可能覆盖导入文件的变量名，两个列表都不包含值。
- **迁移 API 坚持本机边界并支持对账 / 取消**：新增 `POST /api/migration/export`、`POST /api/migration/import`、`GET /api/migration/status`、`DELETE /api/migration/pending`。四者即使在 LLM 降级态也可用，但仍要求后端真实观察到 loopback transport、同源浏览器意图和显式 `X-OBC-Auth: 1`；浏览器扩展、局域网客户端和远程反向代理不能调用。导入另要求 `X-OBC-Migration-Confirm: replace-all`，接受 / 生成 UUID `request_id`；status 在上传 / 校验期返回匹配 ID 的 `processing` 与 `uploading|validating` phase。断连后桌面端最多强制查询 3 次，遇到 `idle/cancelled` 间隔 500ms 再确认，不能把一次瞬时 `idle` 当终局；每次打开配置「通用」也会强制对账。取消只删除 pending、不改 active 数据。guided init 期间导出 / 导入拒绝，status / cancel 仍可用；配置保存忙时导入 / 导出拒绝。
- **在线修改 `data_dir` 改为重启后切换**：`PUT /api/config` 仍持久化用户选择，但 canonical 路径与当前 active data dir 不同时返回 `restart_required=true`；本进程的 `RuntimeContext`、数据库、同次保存的外部 Cookie 凭据和迁移导出的数据快照继续使用已经取得 runtime lock 的旧目录，其它字段照常进入后台应用队列。只有完整重启并取得新目录锁后才启用新路径，`apply-status=applied` 不再被误解为数据目录已热重载。
- **修复启动冷备与 SQLite WAL 锁的顺序风险**：`openbiliclaw start` 现在在 guided init 或 runtime 建立持久 SQLite 连接之前完成健康检查和到期冷备；不再在已有连接后用普通文件复制打开 / 关闭主库 inode，避免 POSIX 进程锁被意外释放并导致后续 checkpoint 使用错误 WAL 世代。迁移导出的 online backup 也改用只读源连接；新增跨进程完整性探针后旧 / 新连接交替写入的回归，确保最终数据库仍通过完整性检查。

- **Issue #112 新增 30 天内容历史**：插件 side panel、桌面 Web 与移动 Web 都新增「历史记录」，按「主动点开过 / 出现过但没点开 / 最近移除」三组分页展示。后端复用 recommendation 点击事件与推荐记录，只为会随 membership 删除而丢失的本地收藏 / 稍后移除保存快照；重复内容按 canonical `item_key` 折叠，最近移除可一键恢复。三端封面统一按页、懒加载、低优先级走现有磁盘缓存代理，避免打开历史时并发请求整月图片。
- **Issue #112 内容库信息架构收敛**：插件、桌面 Web 与移动 Web 将原来的「稍后 / 收藏 / 历史」三个一级入口合并为单一「内容库」，内部保留三个语义子 tab；移动底栏和插件导航都缩为「推荐 / 内容库 / 画像 / 对话」四项。旧 hash、deep link 与 popup `?tab=` 参数会迁移到对应子项；键盘方向键、Home / End、焦点环、44px 触控目标与每个子项的滚动位置保持可用。
- **Issue #112 真实 E2E 加固**：在匿名 B 站实时数据、旧库迁移、三端真实浏览器、断服重启、并发读写与真实图片代理上补齐验证；legacy 推荐先确定 canonical key 再做等值关联，成熟库 `shown` 首屏由约 8 秒降至冷启动约 0.14 秒、热查询约 0.02 秒。历史 URL 只返回安全 HTTP(S)；新界面首屏不发送 cursor，续页使用绑定分类、30 天窗口、全序位置与 source max-id anchors 的 opaque `next_cursor / has_more`，避免头部新增造成 OFFSET 漂移，但不把既有行更新/删除描述成跨请求快照。每个内容的 `contexts` 分别保留 favorite/watch_later/dismiss/dislike 最新事实，收藏与稍后再看独立恢复；坏封面显示可见 fallback，插件读取另有 12 秒截止时间。
- **Issue #112 保存卡坏封面回退**：真实图片代理返回 403 或浏览器已缓存失败结果时，移动 Web、桌面 Web 与插件的收藏 / 稍后再看卡片都会移除破图 `<img>`，在原尺寸位置显示可见 SVG 占位；打开卡片的标题标签和布局保持不变。
- **探针聊天跨界面对齐**：从消息里的「多聊聊」提交的 `probe` / `avoidance_probe` durable turn 现在也会进入插件、桌面 Web 与移动 Web 的主对话历史；关闭消息面板或切换到「聊聊口味」后仍能找回这段对话，惊喜推荐 `delight` 继续保留在自己的内容卡片内聊中。
- **修正 dislike 的产品边界**：普通 dislike 不再被当成搜索词禁令，也不再让跨 digest 关键词整理撤销同词 pending；搜索与多源抓取可以继续宽搜，单卡反馈只同步隐藏该卡，主题证据确认后才约束相关推荐输出。平台库存饱和产生的 supply avoid 仍可独立淘汰冗余关键词。
- **关闭偏好写入到推荐展示的延迟窗口**：`get_profile()` 立即合并 flat preference 的最新 dislikes；推荐历史 snapshot 绑定 dislike digest，首屏、换批、追加、OpenClaw 与主动通知在最终输出边界复核，不再等待 1 秒缓存、Soul rebuild 或异步清池。
- **保留误杀保护与既有语义清池**：结构化 topic 精确命中始终排除，普通模糊命中在多卡全灭时只恢复 exact-safe 行，单条 push 不恢复；embedding + LLM 清池继续作为库存优化而非展示正确性边界。

## v0.3.200：聊聊口味 Markdown 渲染（2026-08-07）

- **Issue #147「聊聊口味」支持安全 Markdown 回复**：popup、桌面 Web、移动 Web 及惊喜推荐 / 探针内嵌聊天统一渲染 AI 回复中的加粗、斜体、列表、代码、引用和安全链接；原始 HTML 与不安全链接会被转义或拒绝，用户输入仍按纯文本显示。已通过真实后端请求与真实 `/web` 页面 DOM 验收。

## v0.3.199：配置应用与保存列表体验修复（2026-08-06）

- **配置保存与后台应用状态统一收口**：`PUT /api/config` 持久化成功后统一立即返回 `202` 与单调 `apply_revision`，由 app-owned latest-wins 队列在后台安全热重载；`GET /api/config/apply-status`、`config_reloaded` 与 `config_reload_failed` 成为桌面 Web、插件和 `/setup/` 的共同状态契约。向导会等待配置真正应用后再进入下一步，热重载失败同时恢复磁盘与内存 last-good runtime；桌面设置页忽略旧 revision 的迟到状态，并在保留新草稿时更新 Discard 使用的 canonical 回滚快照。
- **修复桌面 Web 保存列表徽标首屏缺失与乱序覆盖**：刷新 `/web` 时并行水合稍后再看 / 收藏列表，直接使用后端 `total` 显示侧栏徽标；零值和读取失败继续保持隐藏，不阻塞推荐首页。首屏请求与用户进入列表后的完整刷新通过 per-list generation fence 隔离，迟到旧响应不会把新数量覆盖回去。
## v0.3.198：小红书 discover 后台化（2026-08-06）

- **小红书搜索发现不再抢占当前页面**：search task 改为始终在 inactive tab 执行；MAIN-world bridge 只对小红书搜索接口响应提取与既有 DOM collector 等价的公开卡片字段，并通过页面内 replay 缓存交给 isolated task executor，解决隐藏页不挂载虚拟列表时的空结果。DOM 仍作为 schema 漂移兜底，creator 继续后台执行，只有需要点击个人入口和受控滚动的 `bootstrap_profile` 保持前台。真实登录态连续 3 次搜索均在约 4–5 秒返回 20 条笔记，原活动页面 35 次可见性采样全部保持 `visible`；后端、桌面、浏览器插件、Docker 与聚合 Release 统一使用 `0.3.198`。
- **发布门禁等待 detached saved-sync 完整收尾**：慢速 CI runner 上，测试在 durable `synced` 终态已落盘后继续等待 watchdog done-callback 清理完成，再断言内部 task 集合为空；生产 saved-sync 行为不变，消除数据库终态与事件循环回调之间的时序抖动。
## v0.3.197：来源账号增量同步与登录态可靠性（2026-08-06）

- **五个浏览器账号来源支持可靠的周期增量回拉**：画像就绪且插件在线时，runtime 默认每 24 小时按持久 round-robin 复用小红书、抖音、YouTube、知乎和 Reddit 的既有 bootstrap scope；五源全局串行，并受扩展在线、guided init、来源开关、热重载周期和跨进程 SQLite admission fence 共同约束。任务结果按 canonical result → durable event ingress → seen-key checkpoint → terminal flip 落盘，崩溃窗口可由租约重领修复，重复回拉不会重复学习同一事件。
- **Reddit 与小红书回传边界进一步收紧**：Reddit 补齐 first-final-wins staged ingestion、有界分型 identity 去重和 parent / short URL 防误认；小红书 bootstrap 的允许 scope 与 `max_items_per_scope` 由任务创建时的不可变 payload 决定，partial、final、直接完成和风控失败合并都累计裁剪。扩展重试、分批回传或未知 scope 不能再扩大画像事件预算，已接纳笔记仍可安全补发布时间与首个同 identity token。
- **真实页面登录态识别跟上当前小红书 DOM**：search / creator / bootstrap 除可见登录弹层外，也识别登录手机号输入框与侧栏本人登录按钮，并用完整祖先可见性排除隐藏控件和普通笔记文字。真实已登录浏览器验收确认 `/api/sources/status` 恢复为 `browser_heartbeat / verified`，旧 `web_session` 与新版 `/explore` 登录门不再造成误判。
- **PC Web 已登录后的黄色 Cookie 警示会自动消退**：收到 B 站、抖音、X 或 Reddit 凭据同步 runtime 事件后立即重读来源状态；页面可见时保留 30 秒离线轮询作为漏事件兜底，不再要求打开来源设置页或手动刷新首页。真实 Chrome 登录态 E2E 两次捕获 `bilibili_cookie_synced` 后 1ms 内紧随 `/api/sources/status`，独立只读探针为 `replayed=false / verified`。
- **发布门禁消除并发测试调度抖动**：image-fetch singleflight 回归先等待全部并发调用加入再断言计数，避免慢 CI runner 在任务尚未调度完时误报；生产协调器语义不变。后端、桌面、浏览器插件、Docker 与聚合 Release 统一使用 `0.3.197`。

## v0.3.196：候选池即时补给与模型调用瘦身（2026-08-06）

- **候选池从定时等待改为缺口驱动即时补给**：候选评估器缺少 raw work 时会按平台份额缺口并行唤醒全部已配置 producer；周期 tick 与即时 tick 共用 per-source lock。补池结果显式区分插入量、真实进展与有效产出，重复结果不再冒充成功，而会进入 30 / 60 / 120 / 300 / 600 秒无产出退避；任一候选真正入队即清零阶梯。真实端到端请求已验证抖音来源任务、模型评估、候选入池与推荐库存恢复闭环。
- **通过真实门的 sparse JSON 成为 batch evaluator 默认**：100 条候选 × 3 轮对照中，prompt-token 节省中位数为 `27.99%`，total-token 节省中位数为 `24.05%`，相对质量、分类、repair、route、embedding、recall、usage 与隐私门均通过。生产请求使用 request-local ID 和 canonical sparse envelope，未发送的 URL / 全局 ID 不再造成 cache false miss；完整正文、发布时间、多模态顺序和失败修复契约保持不变。
- **画像二级兴趣按推荐意图治理重复项**：候选门结合 embedding 与保守词面召回，连通分量替代首成员贪心聚类；父子兴趣、跨类通用后缀和用户显式 no-merge 仍受保护。任一模型批次失败都会留下 `retry_pending`，精确 raw Soul 快照支持回滚，apply 前的完整 revision 校验避免长模型调用覆盖并发写入的新兴趣。
- **三端待聊与平台状态恢复更可靠**：插件在后端恢复在线、runtime stream 重连和侧栏重新可见时刷新待聊角标；PC Web 将待聊计数提升为首屏高优先级请求，移动 Web 同步显示底部对话角标。PC 平台 Tab 的配置快照读取增加 1 / 2 / 4 / 8 秒有界重试，瞬时连接重置后仍会收敛到已启用平台集合。
- **发布类型检查在 Python 3.11 / 3.12 间保持一致**：`evaluation_wire` 用显式类型变量承接已验证 JSON 数字，消除 CI 的 `redundant-cast` 与本地的 `no-any-return` 分叉，row-wire 运行语义不变。
- **后端、桌面与浏览器插件统一发布为 `0.3.196`**：组件标签、GitHub 聚合 Release、Docker 镜像和桌面安装包使用同一版本；Chrome Web Store 以替换 pending submission 的方式提交审核，Firefox 继续走独立 AMO listed channel，并附可复现 reviewer source。

## v0.3.194 / extension v0.3.195：小红书真实搜索修复与首启可靠性（2026-08-05）

- **画像二级兴趣去重从“严格同义词”升级为“同一推荐意图”**：真实画像诊断显示，“社会时事 / 时事新闻”“生活日常 / 生活记录”等相近二级项在 bge-m3 下只到 0.77–0.82，旧固定 0.85 候选门根本不会送审；新流程用跨类 0.80、同 category 再放宽 0.04 的 embedding 门叠加保守词面召回，并以连通分量替换首成员贪心聚类，修复 A≈B、B≈C 但 A≉C 时 C 永久落单。跨 category 的通用后缀不连边；dislikes 仍保持 0.85。no-merge 现在只切断已判 pair、不再遮住新邻居，并同时进入 prompt 与代码校验；策略版本升级会重审旧严格口径下的模型 keep，但用户显式 revert 的 pair 独立保护，旧状态可从 run snapshot + changelog 恢复。likes judge 改按“是否重复占用同一推荐/搜索意图”裁决，但父子兴趣仍分开。另修复真实运行日志已复现的失败闭环：`soul.consolidation` 因 provider cooldown 失败后，旧实现仍写 clean digest；现在任一 batch 失败、缺失或校验拒绝都会标记 `retry_pending` 且不写 clean digest，下一 due tick 会重试同一输入。真实隔离 apply/revert 还暴露出旧 run record 只保存 flat preference、回滚时会重建而非恢复原始 Soul 树；新记录现在额外保留完整 raw `soul.json`，先恢复 overrides 再精确恢复 Soul 和有效画像镜像，旧记录继续走兼容重建路径。同期真实 daemon 多轮 preference 写入又验证了 40–60 秒裁决窗口内确有并发更新；apply 现在落盘前校验 active / archived / dislikes 的完整 revision，冲突时不写画像、不写 run/state、下一 tick 立即重试，避免旧合并快照吃掉刚落入的新兴趣。
- **CI 的 MyPy 1.19 / Python 3.11 收窄规则兼容**：`evaluation_wire` 的 JSON 数字解码不再依赖在不同 Python target 下会被判为冗余或必要的 `cast()`，改用显式类型变量承接校验后的原值；row-wire 运行语义不变，同时消除 CI 的 `redundant-cast` 与本地的 `no-any-return` 分叉。
- **修复候选池在重复供给中数小时只补进一条**：候选评估器缺少 raw work 时不再只触发 B 站 refresh，而会按平台份额缺口立即并行唤醒全部已配置 producer；周期 tick 与即时 tick 使用 per-source lock，避免同平台重复抓取。补池结果新增真实 `supply_inserted_count / supply_progress_count / supply_productive` 契约，单纯跑过策略、搜索结果全部命中 durable duplicate 时不再冒充成功，而是进入 30/60/120/300/600 秒无产出退避；任一候选真正入队会立即清零阶梯。统一 admission 阈值和来源配额不变。
- **推荐文案预生成改为有界 copy-ready 水位，且三组合根语义一致**：新增 canonical `copy_ready` / `admitted_pending_copy` readiness 计数，正数 `scheduler.copy_ready_target_count` 只补当前文案缺口，避免每轮排空全部 durable backlog；API RuntimeContext、普通 CLI 与 OpenClaw 都按 `min(max(configured, 0), max(pool_target_count, 0))` 注入同一有效水位，`0` 继续作为 legacy drain-all 的显式回滚值。serve、feedback 与维护提交只发非阻塞 refill 通知，锁内再次核对缺口，已有文案不会因降低目标而删除。
- **候选 evaluator prefilter 补齐隐私安全 shadow 证据与 enforce gate**：默认模式仍为 `shadow`，每个决策只以 candidate hash、平台/上下文类别、相似度/阈值和 embedding/profile digest 落入 30 天 / 20,000 行有界审计表，LLM 返回后再连接原始 score 与统一 admission 判定；标题、URL、正文、prompt、画像文本和 provider response 均不持久化。provider / parse 失败时产品路径的 synthetic 0 不会回填审计，行保持 incomplete 让 coverage fail closed。required-interest 与实际 embedding 输入统一按权重取 top-256；无 embedding service 改用固定域分隔 digest namespace，因此 missing-service 行也可持久化并连接；`profile_interests_missing` 正式纳入 degraded gate。embedding 缺失/异常、向量错维/非有限值及 telemetry 写失败全部 fail-open，显式 `enforce` 在本批决策证据无法完整落库时也不剔除任何候选。只读脚本按冻结 audit id 计算至少 100 条、全局/平台 recall、保护候选 false negative、100% coverage 与 degraded fail-open 证据，报告不会自动开启 `enforce`。
- **修复三端“待聊确认”数字角标偶发不出现**：插件 popup 在启动探测离线后恢复、runtime stream 重连及面板重新可见时都会刷新；PC Web 把待聊数量提升为首屏高优先级请求，避免被推荐卡 saved-status 请求扇出挤进浏览器连接队列，并随实时事件与重连去抖更新；移动 Web 新增底部对话 Tab 数字角标，并在首屏、重连与实时事件后同步。浏览器工具栏角标继续只表示后端健康，不混入待聊计数。
- **修复小红书真实搜索的隐藏页与失效登录双重误诊**：更新后的工作区插件第一次回执显示 `document.hidden=true`、46 个普通 anchor 但 0 个 note anchor；切到前台后 `hidden=false` 仍为空，随后在同一真实浏览器手动提交搜索，页面明确弹出“登录后查看搜索结果”，证明残留 `web_session` Cookie 把失效会话误报成已登录，而非搜索频率直接触发风控。dispatcher 现在先记录当前活动标签，search 短暂以前台标签渲染并在结束后恢复原标签；executor 看到可见登录弹层会立即返回 `xhs_login_required`，后端再把这份真实页面证据写回登录态为 false。creator 保持后台，默认 20 分钟目标间隔（稳定 ±25% 抖动）、每日 20 次预算、队列积压门控和 `1h → 24h` 指数退避全部保留。
- **搜索路由与空结果诊断继续收口**：task、bootstrap、被动采集共用 `/explore/{id}`、`/discovery/item/{id}`、`/search_result/{id}` 三路由 selector；搜索 SPA 最多等待 12 秒。真实空结果仍只回传 pathname、可见性、viewport 与 anchor 计数，不上传搜索词、页面正文、链接、Cookie 或 state 内容。
- **修复首启 bge-m3 下载进度不实时更新（Issue #142）**：安装包在启动拉取线程前发布进程全局 running 状态，setup、桌面 Web 与 popup 在初始化前即可接管并持续轮询下载进度，慢速 Windows 下载不再只显示静态等待。
- **候选评估使用真实发布时间**：单条、批量和补分类路径统一携带来源 `published_at` 与真实 UTC `evaluated_at`，缓存同时绑定发布时间摘要和评估小时桶；缺失或无效发布时间保持中性，不再让模型按知识截止时间猜测当前日期。
- **发现关键词轮换与积压保护增强**：planner 避免重复消费同一关键词，XHS producer 按默认 20 分钟节奏检查，并在 pending + in-progress 搜索达到 5 条时停止 claim 与 LLM 生成。
- **发布版本与 AMO channel 冲突处理**：后端、桌面和首轮 GitHub 插件包发布为 `0.3.194`；该扩展标签的既有自动签名流程先在 AMO 占用了 unlisted `0.3.194`，AMO 因此拒绝同版本转 listed。商店补丁版本提升为 `0.3.195`，Chrome 用它替换刚提交的 `0.3.194` 审核包，Firefox 以全新 listed 版本重提；仓库变量 `FIREFOX_SIGNING_ENABLED` 同步关闭，今后 GitHub 扩展发布不再抢占正式 AMO listed 版本号。
- **PC Web 平台 Tab 集合在配置快照瞬断后自动恢复**：水合时 `/api/config` 与库存快照并行读取，偶发的连接重置此前会被静默吞掉——已启用但零库存的平台（如 Reddit）因此永久缺席筛选行，直到整页刷新。配置快照现在与平台库存一致采用有界重试（1s / 2s / 4s / 8s），成功后 Tab 并集收敛；E2E 桩改用 HTTP/1.0 短连接消除并行水合时的 keep-alive 复位竞态，并新增「配置两次失败后 Reddit 仍以 0 计数出现」的回归用例。

## extension v0.3.193：Firefox AMO 公开商店提审（2026-08-03）

- **修复首启 bge-m3 下载进度不实时更新（Issue #142）**：桌面 `/setup/`、`/web` 与 popup 在初始化前就会接管后台 embedding 拉取并持续轮询；安装包启动拉取前先发布进程全局 running 状态，慢速 Windows 下载无需先点击「开始初始化」即可看到实时进度。
- **PC Web「聊聊口味」补上持续可见的模型等待态**：消息刚提交时立即显示「阿B 正在思考，等待模型回复…」与三点动效；后端创建 durable `pending/processing` turn、历史刷新接管后继续按真实状态显示，不再因临时提示被刷新覆盖而只剩用户消息。回复完成或失败时等待气泡由终态原位替换；状态使用 polite live region、`aria-busy` 并遵守 reduced-motion。
- **修复小红书搜索任务整页 0 条的路由漂移**：2026-08-04 真实插件请求复现 `Python` 搜索 0 条且无风控命中，随后确认 Chrome 商店 0.3.192 的通用适配器已经识别 `/search_result/{note_id}`，但 task executor、被动采集和共享 note selector 仍只认 `/explore/{id}` / `/discovery/item/{id}`。三条采集链路现统一使用同一选择器与 URL parser，后端 observed-urls、内容页分类和原生保存身份校验同步接受第三种 note 路由；搜索 SPA 等待上限由 5 秒调整为 12 秒（仍低于 30 秒 dispatcher timeout）。真实空结果会写 `xhs_empty_result`，并只回传 pathname、生命周期标志和各路由 anchor 数量，不包含搜索词、标题、正文、href、Cookie 或页面 state。真机对照确认在测试浏览器实际运行的是商店包 0.3.192；工作区 0.3.193 构建与回归已通过，修复版真实请求须先把该浏览器更新到新包，`chrome.runtime.reload()` 只会重启当前商店包、不会加载工作区 `/dist`。
- **Firefox 首次公开上架不再复用 unlisted 签名链路**：新增手动 `Submit Firefox AMO Listed Package` workflow，以全局唯一的扩展版本构建 `dist-firefox/`，携带双语名称、摘要、描述、MIT license、合法 Firefox / Android 分类和审核说明提交 `web-ext sign --channel=listed`；0.3.192 已作为 unlisted 版本存在，故公开提审使用独立扩展版本 0.3.193，后端与桌面版本仍保持 0.3.192。
- **审核所需源码与隐私资料和提交包同源**：workflow 从同一 Git commit 打包 `extension/`、共享 Web 模块、lockfile、构建说明和 `docs/privacy.md`，主动通过 `--upload-source-code` 附上可复现源码；提交后查询版本列表并要求 0.3.193 的 channel 真实为 `listed`，避免把上传成功误报成已提审。
- **AMO 隐私字段故障不再反向阻断版本提审**：连续真实请求证明 `eula_policy` PATCH 无论补齐与 `web-ext` 一致的 JSON headers、使用 Gecko GUID 还是 canonical 数字 add-on ID，当前 developer JWT 都只收到无正文 HTTP 406；该非必需字段因此移动到 listed 版本已受理并核验之后 best-effort 同步，失败会在 workflow 留下显式 warning 和 Developer Hub 手动回填指引。manifest 数据类别、reviewer notes、双语 listing 描述、随 reviewer source 附带的 `docs/privacy.md` 仍随提审送达，不会把隐私披露静默省略。

## v0.3.192：多模态推荐、可靠反馈与连接增强（2026-08-03）

### 扩展在线账号信号刷新

- **五个浏览器账号来源支持周期增量回拉**：画像就绪且 installed extension 在线时，runtime 默认每 24 小时按持久 round-robin 复用小红书、抖音、YouTube、知乎和 Reddit 的既有 bootstrap scope；五源任务全局串行，扩展离线、guided init 活跃、来源/调度关闭或周期为 0 时零入队、零时间推进。全局与逐源 `0..168` 小时配置支持热重载和逐源 `null` 继承；成功 init 会种下最近 attempt，失败留给后续在线 tick 自愈。
- **五源 task-result 统一崩溃恢复和有界去重**：Reddit 加入 XHS / 抖音 / YouTube / 知乎的 first-final-wins staged 协议，按 canonical result → durable event ingress → 原子 seen-key checkpoint → terminal flip 执行；任一窗口失败都由租约重领从首写修复。五源 seen key 按真实响应顺序各保留最新 5,000 个，Reddit post/comment/subreddit/user 使用分型稳定身份，重复周期回拉不会重复写事件。
- **独立审查收紧并发与初始化边界**：runtime / CLI / guided init 共用 SQLite `BEGIN IMMEDIATE` admission transaction，五表 active scan 与 insert 在跨 facade / 进程场景也保持单飞，`force` 不绕过；崩溃窗口收养同步任务创建时间与 cursor，非法无时区 / 未来时间戳按到期自愈。`POST /api/init` 在来源 opt-in 热重载前先持久预定 run，新旧 controller 都看得到 init fence；Reddit 扩展与后端同时拒绝把 parent post ID、短 post URL 或仅标题社区行误作 comment/community identity。

### 用户日志暴露的推荐空窗与后台振荡修复

- **候选评估使用真实发布时间**：单条、批量及推荐池补分类 evaluator 都携带来源已有的 `published_at`，并用精确 UTC `evaluated_at` 作为热点、时事和版本更新等时效性判断的权威基准；模型不再根据自身知识截止时间推测当前日期，缺失或无效时间保持中性。单条 / 批量评分缓存同时绑定发布时间摘要与独立评估小时桶，来源后补时间或 daemon 跨小时后不会继续复用旧分数。
- **避雷画像不再把推荐永久杀空**：正常情况下仍用 `disliked_topics` 对结构化 topic 与标题/简介/作者/标签/正文做即时出口过滤；只有模糊子串规则将整个 serve 窗口过滤为零时，才对该窗口降级为 `topic_key/topic_group/pool_topic_label` 精确硬禁用并记录诊断，显式类别避雷不恢复。
- **候选池维护不再恢复/裁剪振荡**：suppressed 恢复受 raw headroom 限制，raw 已满或超限时先裁剪；protected/token-owned excess 已无 victim 时返回 `has_more=False` 并把原 ERROR 风暴降为稳定 WARNING。用户日志中的 AB raw 状态不再每 tick 反复切换。
- **错误模型路由快速失败**：OpenAI-compatible 的 400/403/404/405/422 不再做三次无效 provider 重试；`404 model route not found` 保留完整原因交给 fallback/配置诊断，5xx、timeout 与传输错误继续按原策略重试。
- **稳定画像不再回放同一批搜索词**：关键词 generation cache key 纳入实时 `recent_keywords`，写入前再按大小写/空白/标点做近期词硬去重；候选预过滤若发现某个 `source_keyword_id` 的返回结果全部已存在，会立即把该词退役并保留冷却历史。`recycle_oldest_used` 只允许超过 `history_window_hours` 的历史有效词兜底，避免画像 digest 不变时在 `plan_ttl_hours` 内反复搜索相同内容。
- **灵感词按真实产出轮换并拦截换皮/错归因**：`materialize_platform_keywords()` 先覆盖每个选中兴趣再给同兴趣补第二轴，避免少量兴趣抢完平台配额；selection ledger 延后到装配与近期过滤之后，只记录实际留下关键词的兴趣。输出若冒用未选兴趣、或 core 明确命中另一个画像兴趣会被拒绝；只给旧 query 增加「复盘 / 解析 / 教程 / 盘点 / 测评」等尾缀也归入同一冷却 family。

### 小红书访问节奏与风控背压

- **小红书主动搜索改用保守默认节奏，并补齐四层背压**：新配置或缺键配置的 `daily_search_budget / task_interval_seconds / min_interval_minutes` 从 `0 / 300 / 3` 调整为 `20 / 1200 / 20`；20 分钟是目标值，legacy search / creator 每次 claim 按任务 ID 施加稳定 ±25% 抖动（约 15–25 分钟）并把实际 `next_claim_at` 落 SQLite，避免后端、MV3 或多浏览器 profile 重启绕过，也避免固定周期访问。`XhsTaskProducer` 在 pending + in-progress search 达 5 条时直接返回 `backlog`，不 claim planner 词、不调用 LLM，只按空位补队列；默认每日 20 次与该积压门共同限制高缺口时的持续搜索。风险回调新增持久 `rate_limit_strikes`，独立风控轮次按 `1h → 2h → 4h → 8h → 16h → 24h` 指数退避并封顶；同一活动冷却内重复报告不加轮次，冷却后的正常 search / creator 成功才重置，晚到成功不能取消其它任务刚打开的冷却。状态 API 同步显示连续轮次与剩余时间。插件、桌面 Web、CLI、配置样例、模块文档和架构图已统一默认值；**这些数值是工程安全起点，不是小红书官方阈值，已有显式 `config.toml` 值不迁移、不覆盖**。

### LLM token diet 与 sparse evaluator landing

- **Cognition compact 按真实任务门分拆发布，只默认启用 Awareness-with-confusions**：2026-08-06 SenseTime A/A + A/B 实际覆盖 `build_awareness_with_confusions_prompt` / `soul.awareness_confusions`，不代表普通 `AwarenessAnalyzer.analyze()` 已通过。未发布的 `soul.cognition_prompt_view` 删除，改为 `preference_prompt_view / awareness_prompt_view / insight_prompt_view` 三个独立 `legacy|compact-v1` 字段，默认 `legacy / compact-v1 / legacy`；`awareness_prompt_view` 只进入 with-confusions seam，普通 `soul.awareness` 固定 `legacy`。Config load/save/校验、配置 API、RuntimeContext、CLI 与 OpenClaw 全链路逐项透传。Replay artifact 新增机器可读 `task_rollout`，任务键明确为 `preference / awareness_confusions / insight`，每项独立报告 route、token、quality、blocking reasons 与最终 selected view；`awareness_confusions` 可在全局 full-compact gate 仍失败时单独 enabled，Preference/Insight 保持 legacy，且 Insight 在没有预声明 token 阈值时 fail-closed。
- **新增 evaluator 候选字段瘦身与行协议的两阶段 replay-only 验证链**：`sparse-json` 先保持画像、负例、评分输出和 batch runtime 不变，只把每条候选改成 canonical sparse envelope，使用请求内 `0..N-1` ID，删除 URL、全局 ID、作者别名、逐条来源上下文、平台指标别名及所有空/零可选字段；`row-wire-v1` 再以完全相同的 sparse semantics 为 A，只把候选块改成带固定表头和严格 `\\ / \t / \r / \n` 转义的 22 列行协议。多成员结果禁止 positional fallback，未知/重复/缺失 local ID 进入既有 member repair；封面文字锚点与 image input 同步改用 local ID，图片 bytes/MIME/order 不变。回放 artifact 升为 schema v4，prompt/candidate/image/identity/path 摘要均使用运行级随机盐；provider cache 只作诊断，不成为跨模型正确性前提。相同 100 条静态候选块从 production pretty JSON `114861` 字符降至 sparse JSON `39836`（`-65.32%`）和 row wire `30280`（`-73.64%`，较 sparse 再降 `23.99%`）；这些是上线前字符代理，随后继续由两个独立 100×3 真实 usage/质量门决定是否切换。
- **真实 100×3 门保留 sparse 证据但否决 row-wire 上线**：`sparse-json` 三轮相对 production 的 prompt-token 节省为 `17.12% / 29.19% / 27.99%`（中位 `27.99%`），total-token 节省为 `10.68% / 24.32% / 24.05%`（中位 `24.05%`），相对质量、分类、repair、route、embedding、recall、usage 与隐私门通过。原 auditor 曾把 A/A 控制响应偶发漏一个 `reason` 错归因为 sparse 漂移；`f644bbe9` 将契约归因限定到 changed-transport B，immutable calls 离线复算后无 blocker，artifact SHA-256 为 `f183fce2a98ac9e0edf188c8e741b60ec78652df42b2762735ac31b2507b23f7`。随后 `row-wire-v1` 相对 sparse 的 prompt 中位只省 `2.20%`（三轮 `-4.67% / 2.20% / 9.99%`），低于锁定 `5%`；total 中位虽省 `2.63%`，但 style/franchise agreement 同时低于 A/A floor，且一条 A-arm sparse root call 缺 billable usage，故严格失败。两个 artifacts 各自对 692 个源候选敏感值扫描均为零命中；该对比阶段不调阈值、不切 production、不改 eval-cache namespace 或 `CLAUDE.md`，row 失败 artifact SHA-256 为 `8fc7065df93e2d82e7cd3647b3e0245f9462971ca0e091c823babcfed8b573e0`。
- **通过门的 sparse-json 单独落地为生产 batch evaluator 默认**：后续 landing spec 不改变任何已观察门槛，只采用已通过的字段裁剪/local-ID 臂；`ContentDiscoveryEngine` 默认改为 canonical compact sparse JSON，eval cache namespace 升至 `content-eval-v3`，未发送给模型的 URL/BVID/content_id 不再造成 false miss，保留正文、权威 `published_at`、指标、mode、画像、负例、embedding 与 prefilter 失效边界；顶层精确 UTC `evaluated_at` 和缓存小时桶继续支持时效性判断与跨小时重评。多成员 response 继续严格绑定 request-local `id`，多模态 text/image anchor 同步本地化且图片 bytes/MIME/order 不变。显式 `evaluation_candidate_transport="production"` 保留 pretty-JSON/global-ID candidate rollback 并共享当前时间语义；`row-wire-v1` 因冻结协议不能承载 `published_at` 而明确拒绝，仍 replay-only。API/CLI/OpenClaw 与 8 个策略构造点均继承 sparse，无新增 CLI/config 或架构 wiring。
- **evaluator `json-minify` 单变量真实回放已否决上线**：replay B 仅把 batch evaluator 的 profile layers、negative examples 与 content batch 改为确定性 compact JSON，字段、值、system/output/runtime 均不变。首轮因发现 OpenAI-compatible `json_object` 空内容后的成功重试 usage 会被最终响应覆盖而主动中止，不作证据；harness 随后升级为逐 wire attempt 归因、累加并 fail closed。干净提交 `ad4ba670` 的正式 100 条 × 3 轮耗时 `6227.3s`，46 次格式 fallback 全部计费、3 次瞬时限流均恢复。B 的 prompt 字符/字节减少 `25.05% / 20.81%`；按实际 attempts 的配对中位 prompt/total token 节省 `13.57% / 11.29%`，三轮总计节省 `15.72% / 13.16%`，repair 与 topic/style/franchise 分类门通过。但 admission delta 为 `-6pp / -6pp / +4pp`，中位 `-6pp` 低于 A/A 推导的 `-4pp` floor；B 在两个干净重复中的 provider cache ratio 也分别下降 `32.20pp / 31.98pp`，另有一次 A 限流错误使完整 usage/cache 证据门失败。故不启用通用 `json-minify`：profile/negative 等仍保持原渲染，已通过独立质量门的 sparse candidate block 则单独使用 canonical compact JSON；隐私扫描确认 artifact 不含 100 条候选的标题、正文、原始 ID/URL，失败 artifact SHA-256 为 `873d90c9d46c6b45465201a883044d49d921d54eaa18878e012f377a87c2c8c9`。
- **新增 evaluator `reason-off` 独立真实回放臂，并由实测否决生产关闭**：在不改变生产默认 prompt 的前提下，`scripts/run_profile_diet_ab.py --arm-b reason-off` 以当前生产 reason-diet 为 A、完全省略成功评估结果中的 `reason` 字段为 B；两臂继续使用同一冻结画像、候选、recall、route 与重复 A/A 噪声包络。`c6327506` 上的真实 100 条 × 3 轮回放确认 B 的 reason 输出为 0，但相对 A 的 prompt / completion / total token 分别增加 `30.89% / 38.88% / 31.70%`；B 每轮还多触发 3 / 0 / 1 次成功 member-repair 调用，即使排除有 provider 错误的首轮，两个干净重复的 total token 仍增加 `7.18% / 25.97%`。排序相关性中位数 `0.709700`、admission flip 中位数 `5%` 和三个分类字段 gate 均通过，但准入差值中位数为 `-5pp`，低于 A/A 噪声导出的 `+2pp` floor，因此总质量门失败。结论是保留当前生产 reason diet，不把完全关闭 reason 上线；失败 artifact 仅留本地诊断，SHA-256 为 `f6b7276fc5024faa3a61c2524124bf43644044029fb6c93490a50ea30aaa81b2`。故障哨兵 `evaluation_response_missing` 不属于自然语言 reason，实验不会清除；下游 cap-drop 尚未在 replay 内复刻并会显式标为未测。
- **Phase 3 认知请求按真实调用形状减肥，不裁持久数据**：Preference 自动预算回退从“完整请求按事件比例估算”改为从每个剩余 offset 渲染独立 chunk prompt、二分寻找当前最大可装前缀；这既避免旧 preference 被重复计入，也避免后段局部超长事件把相邻小事件拆成额外调用，显式 chunk size 与合并语义不变。Insight 每轮只把最新 20 条 + 最新 20 条 judged/validated 假设送进模型，去重后最多 40 条，但继续与完整 `insight.json` 合并并保存。2026-08-06 生产只读冻结渲染中，3 条偏好事件由 2 calls/17133 chars 收口为 1 call/9537 chars（`44.34%`），441 条洞察历史由 119679 收口到 68885 chars（`42.44%`）；这些是 tokenizer/provider 无关的输入字符结果，真实 billable usage 另由固定路由门验证。
- **画像 digest 抖动不再默认烧掉尚未使用的安全关键词**：新增 `[discovery].keyword_digest_grace_hours=24`（`0..168`，`0` 独立回滚）。planner 在 due 计算前按平台原子整理 regular pending：当前 digest 优先，旧 digest 只保留宽限内、未命中显式 dislike/平台 avoid、未重复且不超过动态高水位的词；保留行不改 digest 或 inspiration/axis/interest 溯源，claimed/executing/terminal/explore 不动。整理后用 all-digest pending 算水位，并把 retained pending 纳入 history 防同族再生成；能力缺失或事务失败自动硬过期回退。生产只读快照近 24 小时可回收候选为 240 条（B站 210、抖音 30），实际可保留量仍受避雷与 cap 决定。
- **Phase 3 固定日日新真实门最终通过**：`scripts/replay_token_diet_phase3.py` 支持 render-only 与固定 SenseTime 单实例 A/A+B，真实请求固定 temperature 0、单并发错峰、禁跨 provider fallback，并校验 route/usage/结构质量/JSON 修复率/重复假设/完整历史 merge；可选关键词 E2E 只把近期过期词复制到 disposable SQLite，贯通 planner→claim→真实 B站搜索→评估→cache→yield，不写生产库。artifact 禁止事件、画像、prompt、响应、URL、Cookie 和凭据。首次额度类 429 被如实保留为失败诊断；额度恢复后，同一冻结快照在 `openai_compatible/deepseek-v4-flash` 上完成正式重跑：Preference prompt `7211→3986`（`-44.72%`）、total `11103→6500`（`-41.46%`）、调用 `2→1`；Insight prompt `47321→25571`（`-45.96%`）、total `49680→29135`（`-41.35%`），虽 completion `2359→3564`，输入收益仍覆盖增长。兴趣 weighted overlap 为 `1.0`，creator 丢失/幻觉、JSON repair、洞察重复均为 `0`，完整 441 条历史 merge 后为 442 条。关键词 control 为 `1 call / 9098 tokens`，24h grace 为 `0 call / 0 tokens`（`-100%`），复用 30 条后真实 B站搜索得到 15 raw / 13 unique，12 条经模型评估、7 条入池，3 个 claimed keyword 全部 used 且 yield 正确归因。关键词评估出现两次同 provider 的 `response_format` 空正文 wire retry，但没有跨 provider fallback，且不参与 planner `9098→0` 的节省计算；最终总 gate PASS。
- **洞察 prompt 从固定尾窗升级为“保底 + 加权 + 同状态归并”**：持久 `insight.json` 仍完整保留，模型可见视图最多 40 条；latest-8、judged/validated-8 先保底，current awareness/profile 相关性最多 16 条，质量/裁决/重复支持/多样性再取 8 条并补位。NFKC + 英文词/CJK bigram 的纯本地打分不依赖模型或 tokenizer；同 confirmed/rejected/unjudged 状态的近重复只竞争一个 prompt 槽，不同状态分别保留，异常时退回 Phase 3 fixed-20+20。442 条只读生产快照选中 40 条，其中 24 条位于旧固定窗口外、最近 8 条全保留、同状态近重复为 0；render 字符相对完整历史少 `39.44%`，相对固定窗口多 `3.27%`。固定 SenseTime `deepseek-v4-flash` 的最终 A/A+B 门中，weighted prompt `27725` 相对 full `48523` 少 `42.86%`，相对 fixed `26724` 多 `3.75%`；B 为严格 schema、0 repair、9/9 结构有效、0 重复，confidence/evidence 漂移 `0.019/0.139` 均在 A/A 的 `0.112/1.5` 噪声内，完整 442 条 merge 后 444 条。此前一次 fixed A1 返回非数组、另一次 A/B evidence 漂移超门均作为失败诊断保留，没有放宽阈值；passing 隐私安全 artifact SHA-256 为 `932c5d955b7449b88065e8a5aec408966e40e0c02c2fd8ee506ff11b68e75932`。

- **真实 replay gate 升级为 fail-closed artifact v3**：`scripts/run_profile_diet_ab.py` 现在加载与生产一致的 effective profile（user overrides + active speculations）并镜像 archived-topic serialization；按生产 30 条 claim grouping、`source_context=mixed` 和 4096 output ceiling 跑 repeated A/A + A/B。每个 logical run 归因并校验实际 provider / instance / model；embedding exception、空 / NaN / Inf / 维度漂移、recall 不完整及 route / snapshot 漂移都会进入最终 blocking reasons 并非零退出。单个 chunk 遇到明确的瞬时 rate limit 时会按 65 / 130 / 260 / 520 秒指数冷却最多重试四次，重试前恢复候选评估字段；402/余额/计费错误与其它失败不重试，恢复的限流调用仍保留在 route artifact 中，完整 schedule 也写入 artifact。compact / reason-diet / reason-off / json-minify 保留臂都可生成不含正文、完整画像、原始内容/图片 ID 或 secret 的独立 JSON；temperature 固定 0 以隔离采样噪声，同时在 artifact 披露生产默认 0.7。2026-07-18 的旧 replay PASS 继续作废。
- **Replay 限流分类尊重 provider 规范化边界**：真实 compact 验收捕获到 transient HTTP 429 被误判为永久额度问题；根因是 replay 在看到 adapter 已归一化的 `LLMRateLimitError("rate limit exceeded")` 后仍继续扫描 SDK raw cause，而部分网关的原始 429 元数据含通用 `billing` 字段。分类现在止于第一个规范化限流异常：明确映射的 429 / cooldown 可按每 chunk 独立预算恢复，明确映射为 HTTP 402、余额不足或计费错误的异常仍立即失败。最终 clean-commit 回放又证明两次预算不足以覆盖网关“空响应 → 去格式约束 → 关闭 thinking”链中的持续节流，因此只扩展 replay 的有界指数冷却，不改变生产调用或质量门；测试覆盖误导性 raw cause、跨 chunk 独立预算、四次持续 429 后恢复，以及永久额度错误零重试。
- **真实质量门连续校准 compact 边界，48 / 16 进入收益与质量实测**：64 / 12 和画像增长后的 80 / 16 都被严格 100×3 replay 否决；96 / 16 虽完整保留当时 87 项兴趣，但 clean commit `f26c63e5` 的 replay 仍以 treatment flip-rate 中位数 `18% > 16%` control ceiling 失败（Spearman `0.616499` 与 admission `+10pp` 通过）。更关键的是 provider usage 显示 96 / 16 每个标准请求只少 174 个输入 token，100 条 / 4 batch 仅省 696 token（`0.67%`）；实际含坏输出补救的 treatment 反而从 14 增到 17 次调用、total token 多 `14.75%`。因此不把 96 / 16 当 landing 收益，而按明确要求实验性改为 48 兴趣 / 每域 16 specifics、tail recall ranks 49..256。当前 93 项画像的分层 profile block 确定性缩短 `26.02%`，极限 fixture 缩短 `67.82%`；同一真实 100 条候选的完整 prompt 对拍在每条注入 3 个长尾标签后，字符减少 `10.14%`，`cl100k_base` 诊断 tokenizer 估算输入 token 减少 `8308 / 124141 = 6.69%`。不做 recall 的毛节省为 `9.33%`，即 recall 为质量换回约 `2.64pp`。这些只证明收益，最终是否保留仍由未放宽的真实 100×3 replay 决定。失败 artifacts `profile-diet-compact-failed-397fe03e.json` 与 `profile-diet-compact-failed-f26c63e5.json` 均只作诊断证据。
- **真实 Reddit 门否决正文 200+100 截断并完整回滚**：100 条长正文候选 × 3 组 repeated A/A + A/B 中，treatment flip-rate 中位数 `18%` 高于 control ceiling `8%`，Spearman 中位数 `0.192031` 低于 floor `0.632378`，admission delta 中位数 `-11pp` 低于 floor `-3pp`；42 条候选实际受截断影响，正文仅保留 `12.95%` 字符。该结果不是可调阈值的小幅噪声，因此 discovery single/batch eval、recommendation legacy/recovery 分类及 single/batch expression 全部恢复完整 `body_text`；replay 工具同步删除 `body-cap` 正式臂。失败 artifact `data/eval/profile-diet-body-cap-rejected-11f77a64.json` 只作不含正文的诊断证据，不作为 landing PASS。
- **Eval cache v3 形成可恢复的输入闭包**：single / batch key 覆盖实际 prompt-visible content/context（含 `published_at`）、compact profile + 精确 tail-recall pool、negative-examples 内容 digest、embedding namespace、prefilter mode、UTC 评估小时桶与 schema version；正文、指标、来源上下文、发布时间、跨小时、长尾权重或模型 namespace 变化都会 miss。临时 / 部分 embedding recall 失败不写 normal cache；混合平台、不稳定隐式 context 和 vision attempt 整批绕过 per-item normal cache。cache 保存 raw per-item result，franchise/style cap 在 caller grouping 上重放，包含 enforce-prefilter 边界的 cold/warm 结果一致；空 metadata 会明确清除对象旧值。
- **Eval reason 从 prompt 建议升级为 runtime 契约**：single、batch 和 cache hit 共用 `normalize_evaluation_reason()`；`score < 0.5` 与 diversity-cap drop 强制空 reason，`score >= 0.5` strip 后最多 30 个 Unicode code points，`None` 归一为空，其它非字符串 fail closed 并进入 bounded member retry。Prompt 同步标明 reason 仅供内部诊断，不是推荐文案。
- **Rebase 后画像序列化 owner 保持单一**：topic lifecycle archived filtering 与进程开关的 canonical owner 是 `soul/profile_views.py`，API / CLI / replay 共享；`discovery/strategies/_utils.py` 仅保留兼容 re-export，避免 current-main lifecycle 语义在 serializer 搬迁冲突中丢失。
### 多模态推荐与高级配置

- **完整视觉 embedding pipeline**：视觉画像（P1）与关键帧（P3）使用带 cross-clean、contested 区和冷启动门控的 margin 几何评分；关键帧单独开启时也会构建质心，P1 cover bonus 仍由 `visual_profile_enabled` 控制。
- **失败可重试且缓存可追溯**：关键帧、弹幕和 embedding 的瞬时失败不再写永久完成戳；成功空结果与失败可区分，质心、关键帧、弹幕状态绑定 embedding fingerprint、维度和采样签名，模型切换会安全重建或重嵌入。
- **跨平台公平保持零点与符号**：正负 bonus 以 0 为固定点按平台分段对齐，多平台只对齐到当前观测到的全局侧最大值并受组合 cap 限制，单平台不放大绝对幅度，弱正向不会变成负惩罚；关键帧/弹幕预热范围、fetch limit 和完整摘要长度均遵循当前候选池与配置。
- **视觉结果不永久吞失败**：partial keyframe 结果携带 stable sampled-slot，成功槽位先入缓存但不落完成戳；只有 confirmed no-data 或完整采样且所有 embedding 成功才完成。Embedding provenance 同时隔离规范化 endpoint 的 fingerprint 与 L2 namespace；HTTP 200 的 HTML danmaku challenge 作为 transient failure 重试。
- **配置与离线一致性**：`keyframe_max_frames`、`keyframe_fetch_limit`、`danmaku_fetch_limit`、`danmaku_max_chars` 由文件和 `PUT /api/config` 共同校验并 round-trip；`scripts/ab_visual_bonus.py` 与生产评分共享 signed suppression 和零点保持归一化。
- **弹幕摘要严格遵守字符预算**：`condense_danmaku` 将 ` | ` 分隔符计入 `danmaku_max_chars`，保留完整弹幕，单条超限跳过，避免摘要实际长度超过配置预算。
- **桌面 Web 与插件设置页新增统一的「高级功能」Tab**：桌面端 7 个 Tab、插件端 6 个 Tab 都固定提供「推荐增强 / 多模态处理 / 搜索词生成」三个 section；P1/P2/P3 依赖关系、关闭无副作用、七个 discovery 字段的 round-trip 和搜索词三档 option 契约保持一致，调度 Tab 只保留真正的调度项。
- **设置页保存按钮只在有改动时启用**：桌面 Web 与插件 side panel 在配置无变化或保存请求进行中都会禁用保存，避免无操作的完整 `PUT /api/config` 和无意义热重载；输入、LLM 实例/调用链草稿及候选池建议比例等程序化修改都会进入脏状态。保存成功后按钮重新禁用，失败则保留改动并允许重试。
- **配置保存不再被长对话拖到 60 秒超时**：`PUT /api/config` 仍先校验、快照并落盘；检测到对话、结算或事件 owner 正忙时改为立即返回 `202 apply_state="queued"`，由 app-owned latest-wins 队列在后台安全 drain 后热重载。连续保存只保留最新待应用修订，旧修订失败不会覆盖新文件；最终成功/失败通过 `config_reloaded` / `config_reload_failed` 推送，并可由 `GET /api/config/apply-status` 回读。最新修订失败会恢复最后一次已生效配置，初始化在应用完成前返回 `409 config_applying`，插件与桌面 Web 分别展示“已排队”及最终失败回执。
- **插件配置保存栏固定在视口底部**：side panel 不再把位于长表单末尾的 sticky 保存栏留在页面底部；保存状态和按钮始终固定在扩展视口底边，滚动容器同步预留安全区与操作栏高度，最后一项配置仍可完整滚到按钮上方，不会被遮挡。
- **高级功能默认值统一**：新配置的搜索词生成默认使用“混合”（经典 merged planner + search-backed 灵感轴），桌面 Web、插件和 API 缺省回退同步为 `hybrid`；图像 Embedding、候选封面 LLM 评估、P1 视觉画像和 P3 关键帧继续全部默认关闭，已有显式配置不被覆盖。
- **补充 PR #135 贡献者致谢**：README 中英文与贡献指南正式记录 [@wuwafly3](https://github.com/wuwafly3) 对用户视觉画像（P1）、B 站弹幕语义（P2）、视频关键帧（P3）和跨平台视觉加权管线的贡献，并保留其此前在 [#100](https://github.com/whiteguo233/OpenBiliClaw/pull/100) 中完成的 DashScope 多模态 embedding 与封面 image-only 向量归属。
- **原生保存批任务顺序固定为快照顺序**：显式 `item_keys` 的 caller order 继续写入 `native_save_task_items.ordinal`，执行查询也改为按该 ordinal 读取；不再受秒级 `saved_memberships.added_at` 影响，避免同一批次在不同机器上随机先处理后加入的项目，并让 heartbeat 失败时的取消/释放行为保持确定。

### 连接、部署与界面

- **README 与 GitHub Pages 首页恢复真实使用截图**：推荐、反馈、画像、对话、桌面端、移动端及首屏 Hero 全部换回真实运行记录，不再展示 Chrome Web Store 演示夹具生成的虚构内容；演示素材抓取脚本同时移除 `--refresh-docs` 入口，并拒绝把 demo capture 写入 `docs/images/`，避免再次覆盖真实截图。
- **对话 turn 绑定安全性收口**：三端把卡片/疑惑作为 durable turn，通过 `reply_to_turn_id` 只声明目标；服务端在 user INSERT 前冻结 canonical context、digest 和 ordinary/detached mode，统一贯穿 prompt、event、学习与结算，A→B replacement 只会安全 stale-drop。新增只读 context preview、opaque evidence 过滤、独立长列表滚动、reply quote 与失败草稿保留；恢复长历史时首次进入会落到最新消息，后续实时刷新仍保留阅读位置。真实三端 E2E 补齐结算态收尾：卡片已确认/修正/拒绝/稍后时会静默清除旧 context，已知服务端错误优先显示中文；移动端固定回顶按钮按 360px 窄屏重新留距，不再与发送按钮相交。
- **修复 GitHub Star 数量请求偶发 403**：桌面 Web 和扩展不再从浏览器匿名直连 GitHub REST API，统一改走公开同源 `GET /api/project-stats`；后端持久化 12 小时缓存、使用 ETag 条件请求，并在 403 / 429、断网或 GitHub 异常时按响应头退避、返回旧缓存或无数量的本地 200。GitHub Pages 静态官网没有可用的同源后端，因此保留 Star CTA、停止动态请求数量；所有浏览器入口都不再产生 GitHub 失败资源日志，点击仓库行为保持不变。
- **桌面惊喜推荐把“× / 看过了，不再推荐”移到卡片右上角**：关闭动作不再夹在喜欢、不感兴趣、稍后看和收藏之间，避免被误认成同级反馈或误触；按钮保留原有永久已读语义、可见键盘焦点与禁用态，窄屏触控区域不小于 44×44。
- **修复 X「测试连接」只读旧健康记录的问题**：设置页现在通过 `twitter-cli` 的只读账户状态请求即时验证 `auth_token` / `ct0`，401、403、429 和传输失败分别保持失败或待判定语义，不把网络故障误报成 Cookie 失效。
- **修复后端启动后 X Cookie 同步滞后**：启用 X 时，扩展每次新建 `/api/runtime-stream` 连接都会收到 `x_cookie_sync_requested`，立即把当前浏览器 Cookie 回传；原有启动、变更监听和小时 alarm 继续作为兜底。
- **移除扩展临时调试日志中继**：抖音任务仍通过正常的 `task-result` 回传结构化诊断，但不再向 `/api/sources/_debug/log` 额外发起请求；废弃 helper 和后端 relay 路由同步删除。
- **新增最简公网 HTTPS Compose overlay**：`docker-compose.https.yml` 可叠加到源码或预构建部署，使用固定版本 Caddy 自动申请 / 续期公网证书并转发 REST / WebSocket；Caddy 与后端共享 network namespace，宿主机 `8420` 收紧到 loopback，Uvicorn 仅信任 `127.0.0.1` 的 forwarded headers，证书数据使用 named volumes 持久化。Caddy 在 `/api/auth/status` 明确返回密码门禁已启用前不绑定公网端口，首次配置 fail closed；远程扩展仍使用独立设备密钥，现有 LAN/self-managed `tls` profile 保持独立、默认关闭。
- **修复桌面 Web 的 HTTPS 手机二维码降级**：从非 loopback 的 HTTPS 页面打开「手机版」时，二维码现在保留当前 scheme、host 和端口，不再被 `/api/qr-info` 的后端私网 IP 覆盖或硬编码成 HTTP；IPv4、裸 IPv6 与 URL 方括号 IPv6 loopback 都继续实时探测 LAN IP，插件和桌面局域网扫码行为保持一致。
### 反馈、对话与事件可靠性

- **事件幂等 ID 采用严格字符串类型**：三个公开事件入口不会把数字、布尔或其它 JSON 类型自动
  转成字符串；这类输入与缺失、空白、超长值一样在 route 前 422 且零写入。
- **修复事件消费与对话结算并发时 SQLite 抛出 `another row available`**：真实隔离运行中 chat reply 已完成，dialogue settlement sequence=1 却在锚释放更新 durable turn 时失败；根因是 `check_same_thread=False` 只取消线程归属检查，并不允许 status/event reader 与 settlement writer 同时 step 同一 connection。`Database` facade 现在保留初始化线程 primary，并为其它调用线程缓存独立 WAL connection；普通 facade 调用在主线程/worker 都保持既有 foreign-key PRAGMA，显式 `open_connection()` 的原子短事务继续单独开启约束。并发写由 SQLite `busy_timeout` 协调，不增加会阻塞 event loop 的全局 mutex；通用 write helper 在任何 `OperationalError` 后先 rollback，再 lock retry 或抛出。全仓验收随后暴露同一边界下的隐性事务泄漏：XHS token backfill 的两个 UPDATE 若都命中零行，SQLite 仍开启隐式写事务，旧代码却因 `updated==0` 跳过 commit，让已结束的 request-thread connection 永久占 writer lock；现在每条 best-effort direct DML 成功 execute 后无条件 commit、异常 rollback，self-info purge 异常路径也清理事务。回归覆盖 active reader + settlement writer、跨线程 recommendation 兼容语义，以及 zero-row request 返回后主线程立即写入；受 actual asyncio Task identity 保护的锚 mutation 仍只在唯一 settlement worker 内执行，不向线程 child 扩权。
- **聊天回复不再因请求超时永久失败或被并发历史串线**：`POST /api/chat/turns` 只落 pending + wake 后立即返回；app-owned durable reply scheduler 在启动时分页恢复全部 pending，并以单 worker 严格按 SQLite rowid 回复。provider、限流、配置、超时和 shutdown cancellation 保持 pending、原位有界退避，只有显式空/无效响应才 failed；completed/failed 均用 pending CAS，近崩溃模型调用可至少一次但可见终态只发布一次。惊喜、legacy chat、兴趣与避雷探针也进入同一个 app-stable max-active=1 dialogue lease，回复及必要副作用完成前不释放；热重载先停 admission、排空旧 owner，再发布新 runtime，等待请求恢复后动态解析新 dialogue/speculator，25 分钟超时则零 rebuild、恢复旧 lane。`/api/runtime-status` 新增真实 durable depth/active/error/processed，降级态仍可见且不泄漏消息内容。
- **修复连点点赞 / 点踩 / 聊一聊时 feedback 请求卡死或超时**：`/api/feedback` 过去在响应内同步等待统一兴趣线的偏好 LLM（真实一轮 30–76 秒），桌面端 30 秒先回滚后，后端仍可能成功，刷新又显示已写入；连续点击还会排出并发分析。现在成功边界明确为 event-first 的两次 commit：先按 `request_id` 提交 `events.feedback`，再用下一次独立 commit 更新 recommendation 展示投影；二者不是跨表原子，第二步失败会让请求失败，同 `request_id` 重试命中 duplicate event、校验 durable payload 后补做投影，冲突 payload 返回 409。HTTP 随后只 wake，不再获取 pipeline 锁；app-owned `EventProcessingScheduler`（旧名 `FeedbackBatchScheduler` 为兼容 alias）先由 generic cursor 领取普通事件/推荐点击，再由 content-feedback cursor 领取 `/api/feedback` 与 `/api/events` 的显式内容反馈（`like/dislike/comment/dismiss`，且不是 import snapshot）。两者都用 event row 稳定 signal ID，通过 `checkpointed_enqueue_batch()` 将 buffer+cursor 原子发布到同一份 `pipeline_state.json`，随后调用 `tick_if_buffered()`。hypothesis feedback 与 Bangumi 等导入反馈已有自己的学习 owner，只推进 cursor、不进强信号或 generic 增量路径；retraction 仍在 generic cursor 前完成折价。5 秒 safety scan 与启动 recovery 覆盖 commit→wake、scan→checkpoint 或 checkpoint→consume 任一窗口，恢复且不双计；`unified_interest_line=false` 的旧批线路径不变。升级边界分别用 owner version/cutover event ID 按 SQLite **最大 row id**（不按可乱序的 `created_at`）fence 旧 direct-ingest 行；`feedback_state.json` 只作迁移 provenance / 兼容镜像，owner 权威位于 pipeline checkpoint。
- **后端首次启动不再等待 event recovery 或画像 LLM**：lifespan 只同步完成 owner cutover fence、本地 durable 准备和 scheduler task admission，随后即暴露 listener/health；真正的 event scan、buffer checkpoint/consume 与 provider 调用由 app-owned background task 继续。provider 401、慢请求、pending buffer 或永不返回的 LLM 都不会卡住进程启动。shutdown 仍由 scheduler cancel+gather，不遗留 task；配置热重载继续同步 pause/drain/recover/rebind，确保旧 owner 清空后才发布新 runtime。实现没有给 `_process_once()` 套短 timeout 或粗暴截断，cursor、buffer、retraction 与崩溃恢复语义保持不变。
- **三个公开事件写入口拒绝空幂等键**：`/api/events` 每项 `event_id`、`/api/feedback` 与 `/api/recommendation-click` 的 `request_id` 都先 trim，再要求 1–400 字符；缺失、空串、纯空白或超长统一在 route 前返回 422，不产生 event、seen ledger 或 recommendation 投影。相同 ID 的不同 payload 继续保留 409。MV3 buffer、popup、移动 Web 与桌面 Web 都在动作首次创建时持久化 ID，失败/响应丢失重试复用、成功后才删除；CLI feedback 省略时生成并打印 ID，跨命令重试需回传 `--request-id`；OpenClaw CLI/skill 把 ID 设为 required。
- **画像流水线新增持久 enqueue 边界并串行 LLM 消费**：`ProfileUpdatePipeline.enqueue()` / `enqueue_batch()` 只执行撤销预处理、buffer append/evict、轻量 observe 与状态落盘，不触发分析；`ingest_batch()` 复用同一 locked helper 后再消费 ready layer。event owner 使用 `checkpointed_enqueue_batch()` 同次发布 buffer+cursor，随后以 `tick_if_buffered()` 恢复消费；空 owner pass 不跑 speculator/cognition。独立周期画像维护仍调用 `tick()`。`ingest_batch` / 两种 tick / `flush` 的 layer drain 继续由 `_ingest_lock` 串行，同一时刻最多一轮 layer LLM，且成功 drain 会在 maintenance 前立即保存，避免后续维护失败导致重启重放已应用信号。
- **移动端反馈提交补 30 秒超时，惊喜卡失败不再假装成功**：移动端 `submitFeedback`（点赞 / 点踩 / 聊一聊评论）此前没有超时，后端异常时会一直转圈；现在与桌面端一致设置 30 秒上限。惊喜卡「喜欢 / 不感兴趣」请求失败时，移动端不再本地移除卡片或标记已发，而是保留卡片、显示“操作失败，请重试”并恢复按钮；桌面端同样保留卡片并提示可重试。
- **工具栏角标只表达后端健康，不再把待聊数误当未读消息**：service worker 删除 `/chat/pending-confirmations?count_only=1` 轮询、数字状态、runtime 去抖刷新和 30 秒 alarm 附带刷新；健康且已初始化时 action badge 恒为空，后端不可达 / 未初始化仍按原优先级显示浅灰 / 橙色 `!`。popup 与桌面「对话 → 待聊确认」内部计数、列表和打开接口完整保留。
- **封面抓取不再被后台预取占满，也不会并发重复下载同一签名图**：新增 app-owned `ImageFetchCoordinator`，`/api/image-proxy` 前台 miss 与 refresh 预取共用总 active≤4 / background≤3 的 Condition priority gate，始终保留前台槽且 queued 前台优先；按剥离 XHS 轮换 token 后的 cache key singleflight，前台加入 queued background 同 key 会提升优先级并采用更新签名 URL。所有 waiter shield owned task，单请求取消不影响其他消费者；热重载新 controller 重绑同一实例，shutdown 先停 producer 再取消 active/queued work。cache glob/read/write 与 DB target scan 卸载到线程，命中不占网络槽；写盘改为同目录 tmp+flush+fsync+replace，失败只见 old-or-no file。proxy 保持 `X-Image-Cache`、类型、10MB/redirect/白名单与国内直连/境外代理语义；日志和 runtime counters 只含 host/cache hash/error kind 与整数，不泄漏 signed URL/token。
- **四个扩展来源任务改为崩溃可恢复的两阶段完成**：XHS / 抖音 / YouTube / 知乎最终 callback 先在 SQLite 写锁内冻结第一份 canonical result，再从持久结果投影 durable event 与 seen-key，最后才翻 `completed`；staged row 对并发 partial/final/fail/rate-limit 为逻辑终态，但保留普通 claim lease 的 stale reclaim，dispatcher 丢失非 2xx result 响应后也会自动重新领取并修复。三个 commit 间隙都可恢复，变化后的新 callback payload（含 XHS `self_info`）不会污染首写；seen-key 使用严格写后验证，event 已提交但 marker 失败时依靠跨 task 稳定 ingest key 去重。
- **三端交互 request identity 排除可变展示字段**：惊喜反馈不再把 title 纳入 durable pending ID；推荐点击在有 `content_id/bvid` 时不再纳入签名/轮换 `content_url`，响应丢失后即使重渲染标题或 XHS token 已变化也复用同一 request ID。API、CLI 与 OpenClaw 统一把 event 首写 + recommendation 投影作为反馈提交成功边界，画像 owner/认知 follow-up 暂时失败只报告 queued/warning，不把已提交操作误报失败。

## v0.3.191：对话与实时连接稳定性修复（2026-07-30）

- **修复桌面端夜间模式下账号同步提示过淡**：同步异常提示改用主题前景色和明确的状态底色，深浅主题下都保持可读。
- **修复桌面端惊喜推荐文字卡在夜间模式下难以辨认**：知乎、Reddit 等无封面内容不再把前景色当作渐变背景，改用主题表面色、轻平台色和明确的主题前景色；普通无封面文字卡也同步收敛到同一套高对比度样式。
- **补充社区贡献者致谢**：在贡献指南中记录 [@RayeLouis](https://github.com/RayeLouis) 对扩展服务端认证权威判定（[#132](https://github.com/whiteguo233/OpenBiliClaw/pull/132)）和可选 TLS 反代初版（[#136](https://github.com/whiteguo233/OpenBiliClaw/pull/136)）的贡献。
- **修复 Web 与插件聊天不同步，并补齐长页面回顶入口**：插件、移动 Web、桌面 Web 的主聊天统一使用 `session=popup&scope=chat`；聊天界面可见且在线时约每 2.5 秒增量读取共享 durable history，快照未变化不重绘，用户阅读旧消息时保留滚动位置。移动 Web 与桌面 Web 同时增加固定「顶部」按钮，页面滚动区和聊天内滚动区均可一键回到顶部。
- **修复桌面端夜间模式下账号同步提示过淡**：同步异常提示改用主题前景色和明确的状态底色，深浅主题下都保持可读。
- **修复桌面端惊喜推荐文字卡在夜间模式下难以辨认**：知乎、Reddit 等无封面内容不再把前景色当作渐变背景，改用主题表面色、轻平台色和明确的主题前景色；普通无封面文字卡也同步收敛到同一套高对比度样式。
- **修复桌面「聊聊口味」卡片一多就被压扁、无法继续下滑**：对话记录是一个固定高度的 CSS Grid，新增确认卡后隐式行会共同压缩；卡片自身又是 `overflow:hidden`，真实约 217px 的内容会被压成约 44px 并直接裁掉，滚动容器因此也算不出完整内容高度。现在对话记录和待聊列表都使用内容自然高度行，聊天区保留独立纵向滚动并恢复可见细滚动条；待聊列表限制在视口 32% / 300px 内独立滚动，不再挤掉聊天记录和底部输入框。刷新只在读者原本位于底部时跟随最新消息，向上阅读时保留位置，已展开的「依据」也不会因后台重绘自动合上；对话记录补键盘可聚焦的 region 语义。真实 Chromium 回归覆盖 9 张长卡片、10 条待聊项及 375 / 768 / 1024 / 1440px 四档视口。
- **「聊聊口味」三端补齐同一套真实卡片体验，并隐藏无意义的 ID 证据**：移动 Web 原先只读取 `session=popup&scope=chat`，会把同一 durable 历史里的 `hypothesis/confusion` 全部过滤掉，因此即使待聊 open 的真实 POST 成功也看不到卡片；现在移动端和插件一样按 session 读取完整对话流，补齐待聊列表、主动打开、四动作、`202` 按需轮询与跨端终态投影。桌面 Web、移动 Web、扩展 side panel 都使用内容自然高度的卡片和有界独立滚动，刷新保留读者位置与已展开依据，移动端同时保留草稿/焦点并采用两列 44px 动作。共享 renderer 会过滤纯数字、UUID、事件 / note 前缀、BVID、裸哈希等只有机器 ID 的依据，若没有可读说明则整块「依据」不显示，durable payload 和内部证据链保持不变。真实后端 + Chromium + 实际 MV3 扩展验收跑通三端 GET/open/action：桌面展开 10 条依据后的记录区 `494/838px` 且滚轮到达 `scrollTop=344`，移动端 3 条待聊独立滚动并打开真实卡片，扩展提交「稍后」后移动端刷新同步显示已结算；三端 composer 都保持在视口内。
- **修复保存配置时待聊按钮失效、热重载误回滚和 Web 实时流反复报断开**：生产日志显示长达 235 秒的对话学习占住唯一结算 worker，旧热重载会先停接新任务、只等 30 秒，于是连续丢弃 `confusion.open.sync`，再用空白 `TimeoutError` 回滚配置；现在队列保持接单直到真正排空，再原子暂停交接，安全等待窗口与 20 分钟 LLM 上限对齐到 25 分钟，桌面/插件在超过前端 60 秒预算时明确显示“仍在后台热重载”。待聊 open 会在 worker 忙时返回带 `Retry-After` 的 `dialogue_busy`，两端显示等待态并自动重试，required local job 不再留下“已 claim、未建 turn”的半截状态；若已有跨 session 的 `clarifying` 疑惑，列表只给尚未展示该 turn 的 session 暴露当前持有者，不再列出必然 409 的下一条。真实浏览器 E2E 进一步发现 pending-open 卡片仍是 `pending` 状态，点“稍后”虽改成 deferred 却不会走旧 `discussing` 解锚分支；现在会按 origin turn 精确释放同代锚，下一条待聊不再被不可见旧锚挡住。`runtime-stream` 每 20 秒发送空闲心跳，桌面端短暂关闭显示“重连中”、记录 close code/reason 并按原 3 秒节奏续连，不再把 WebSocket 抖动误报成整个后端离线。
- **修复扩展认证状态以服务端判决为唯一权威**：服务端 `/api/auth/status` 返回 `authenticated: false` 时（如 `auth_epoch` 升级或密钥撤销后），扩展设置页过去因 `readPopupSessionToken()` 仅校验本地过期时间而误显“设备已配对”并隐藏密钥输入栏，与推荐 tab 的 401 状态矛盾。现在 `checkAuthStatus` 以服务端 `authenticated` 为唯一权威，服务端未认证时直接展示输入栏。新增 3 个回归测试。
- **新增默认关闭的 LAN/self-managed TLS 反代，并补齐可持久化配置/CLI/Docker 入口**：`[tls_proxy]` 现可完整 load/save round-trip，环境变量 / `config.local.toml` 逐字段覆盖不会被无关保存烘焙进基础配置；`tls-proxy enable/disable/status` 会真实持久化。`serve-api --tls-port` 可临时覆盖 8443。源码 Compose 通过 `tls` profile opt-in，使用 `OPENBILICLAW_TLS_SAN_NAMES` 明确传入逗号分隔远程 SAN、`OPENBILICLAW_TLS_PORT` 同步端口映射；非默认 build-time `CERT_DIR` 会作为容器运行时证书根目录完整传入，未提供远程 SAN 的自动证书只承诺 localhost。插件手机版二维码和 `/api/qr-info` 探测现在保留已配置的 HTTPS scheme，不再把明文 HTTP 发往 TLS 端口；未知 scheme 安全回落 HTTP。转发主体使用 Python 标准库，自动证书生成来自可选 `[tls]` extra / 容器内 `cryptography`，不再误称“纯标准库”。
- **TLS 安全与启动可靠性加固**：HTTPS Web Origin 必须与请求 Host 的 host+port 精确同源（覆盖 IPv4/IPv6/默认端口），合法 Chrome/Firefox 扩展 Origin 继续放行；TLS 出口保留重复 `Set-Cookie` 并补 `Secure`，CA/服务器私钥路径硬拒绝，HTTP/1.1 HEAD/空响应/hop-by-hop header 与真实 WebSocket 双向 relay 有本地集成测试。已有证书缺新 SAN、cert/key 半残、SSL context 或端口 bind 失败都会在 uvicorn 启动前 fail loudly，绝不静默覆盖自有证书或假报 HTTPS 成功。详见 `docs/modules/tls-proxy.md` 与 `docs/https-deployment.md`。

## v0.3.190：启动体验升级（2026-07-30）

- **Windows 启动加载窗改成完整品牌启动卡**：原先 440×168 的纯色横条只有图标、应用名和一行等待文案，信息层级单薄。现在升级为 560×280 的深色渐变卡片，直接使用最新粉色猫爪品牌源图，加入柔和光晕、启动状态区和无虚假百分比的活动进度轨；右上角会在打包时读取 `pyproject.toml` 并展示当前 `vX.Y.Z`，字体缺少中文时仍保持英文降级。版本读取、传入版本渲染和品牌像素均有回归测试。

## v0.3.189：图标与体验收尾（2026-07-30）

- **桌面「聊聊口味」不再退化成裸文本和默认按钮**：共享确认 renderer 原本只有插件弹窗样式，桌面端的待确认条目、猜测卡片、依据和四个动作因此全部按浏览器默认样式平铺。现在桌面「待聊确认」直接对齐插件的紧凑品牌色折叠条、数字徽标、轻量箭头和单列小卡片，不再使用桌面仪表盘式重容器；猜测卡片补齐状态化边框和主次动作，对话气泡更紧凑，输入框也增加可访问名称和稳定 focus。430px 以下动作改两列，深浅主题与 reduced-motion 继续复用现有令牌。
- **初始化期间也能测试 LLM 与 Embedding 配置**：`/api/config/probe-service` 只在配置内存副本上构建临时服务并真实探测，不写盘也不热重载，过去却因使用 POST 被 guided init 的 deny-by-default 写端守卫误判为冲突，插件只能显示 `/config/probe-service request failed: 409`。现在该端点成为精确只读例外，实例、默认调用链、embedding 与网络测试在画像初始化期间保持可用；LLM 探测仍经过稳定 total gate，真正保存配置的 `PUT /api/config` 继续返回 `409 init_running`，不会替换本轮任务正在使用的组件。
- **彻底清理所有品牌图标入口的残余白边与旧图**：透明源图的半透明边缘过去仍携带旧白底 RGB 消光色，缩到 16–42px 会形成浅色晕边；根 `/favicon.ico`、PWA `maskable`、Apple 主屏幕图标、社交分享图、Chrome Web Store 素材和 README / 官网历史截图又各自保留了透明角或旧字母 `B`。现在扩展补齐 32px 精确尺寸，16 / 32 / 48 / 128px、根 favicon、PWA / Apple 全底色图标与页面头图统一用品牌粉承接透明角；普通 PWA 图标继续保留透明用途，专用 maskable 图标使用不透明底色，避免系统裁切露白。中英文社交分享图、商店三张成图与 14 张桌面 / 移动 / 插件文档截图均由本地确定性脚本重采集并重建；favicon URL 升版清缓存，像素测试锁定每种尺寸、透明策略和入口引用。
- **惊喜推荐“×”现在等于看过并永久去重**：现场复现显示同一 canonical 内容已经进入 `seen_items` 后，普通推荐会排除，`get_delight_candidates()` 却仍返回，导致看过、点赞或收藏的视频继续占据惊喜栏位，只有再次点开触发 `delight_notified` 才消失；移动 Web 原有“×”更只删内存，刷新必回。现在 delight 动态阈值、打分 backlog、候选计数和最终出队全部硬过滤 `seen_items`；三端叉号统一标为“看过了，不再推荐”，通过 `dismiss` 先写 canonical seen ledger、再消费惊喜状态，普通/惊喜两条推荐都不再出现。移动端与扩展叉号采用至少 44×44 触控目标、明确 aria-label、可见 focus 与提交中禁用，写入失败不再假装成功移除。
- **修复抖音 discovery 把任务故障当空结果、误消费关键词和 feed 长期饿死**：插件 search / hot / feed 现在把真实空结果、超时、失败与预算耗尽分开回传；producer 按每个关键词的真实终态分别 `used / failed / pending`，保留预算耗尽前已成功的候选，瞬时故障无损重排且不增加 attempts，统一关键词池暂空也不再阻断独立 hot / feed。大缺口改为 search + hot/feed 逐轮轮换，避免 feed 永远没有调度机会。`discover --source douyin` 已切到与 daemon 相同的正式 producer、统一关键词和待评估候选链；`search-douyin` 读取配置预算（`0` 为无限），并以非零退出码暴露 timeout / failed，真实空结果仍成功退出。
- **应用图标去除白色方边并进入启动页**：品牌源图从实色白底改为透明外缘，扩展 16 / 48 / 128px、PWA / favicon 192 / 512px、Windows `.ico` 与 macOS `.icns` 全部从同一透明源图重新派生，系统托盘、菜单栏、桌面和深色背景下不再露出白色方框；Windows PyInstaller 启动页同步加入同款粉色猫爪图标，不再只有应用名和启动文案。透明角与启动页品牌区域新增像素级回归测试。
- **PC Web 自动续页不再跳回平台 Tab**：用户点过平台 Tab 后再用滚轮浏览到底部，Tab 会继续持有键盘焦点；续页完成时重绘库存徽标，旧实现用普通 `focus()` 恢复焦点，Chromium 实测会把 `scrollY` 从 4849 拉回 85。现在重绘仍保留无障碍焦点，但通过 `preventScroll` 阻止浏览器改写视口；新增真实 Chromium 回归，锁住“焦点保留、滚动位置不变”两条约定。
- **小红书关闭后不再继续开搜索页，并增加安全验证熔断**：来源开关过去只停掉新关键词生产，已经写进 `xhs_tasks` 的旧 search / creator / bootstrap 仍可被 `/api/sources/xhs/next-task` 领取，扩展因此在用户关闭小红书后继续打开页面；现在 claim 端每次现读热重载后的 `sources.xiaohongshu.enabled` 与全局 scheduler，关闭时旧任务保持 pending、返回 204，重新开启后再恢复，用户显式 native-save 与 discovery 开关保持正交。真实扩展 E2E 还发现跨来源互斥顺序相反：抖音占锁时 XHS 会先 claim、再因锁忙返回，留下没有 tab 和回调的 `in_progress`；dispatcher 现改为先取得共享锁再请求 `/next-task`，锁忙时不会触碰后端队列。扩展新增可见安全验证 / 操作频繁 / 429 分类器，命中只回传结构化 `rate_limited`、不上传页面正文；后端用单行 SQLite 状态持久化 search / creator 任务间隔和 1 小时平台冷却，跨 FastAPI / MV3 重启及多浏览器 profile 生效，冷却期间 producer 和所有 XHS claim（含 native-save）暂停，关联 planner 关键词无损退回 pending 且不增加 attempts，来源状态显示剩余冷却时间。新增后端队列/API/native-save/producer/keyword 生命周期测试与扩展 executor/dispatcher 误报回归。
- **小红书自动任务默认间隔提高到 5 分钟**：`task_interval_seconds` 默认值从 45 秒改为 300 秒，并同步后端模型与异常兜底、桌面 Web、插件设置页、示例配置和架构文档；现有配置可通过设置页热更新，无需重载插件。

## v0.3.188：降级配置原地恢复（2026-07-29）

- **修好 LLM 配置后不再要求重启后端**：安装包覆盖安装本来已经退出旧 `OpenBiliClaw.exe` 并启动新版本，但新进程会先读取保留的旧 `config.toml`，若其中仍有启用但缺 Key 的实例就以 degraded 恢复态启动；用户随后在 `/setup/` 修好配置时，`PUT /api/config` 过去只写盘并返回 `restart_required=true`，导致刚覆盖安装完还要再手动重启一次。现在降级上下文复用启动时保留的数据库、MemoryManager、事件总线和稳定 LLM total gate，按正常热重载的原子构造路径一次性补齐 Registry、Soul、Discovery、Recommendation、来源客户端与 runtime controller；全部构造成功后同步解除 API 503 guard、刷新 `app.state` 镜像、重绑 feedback scheduler 并启动所需后台任务，返回 `reloaded=true / restart_required=false`，`/setup/` 当场进入下一步。核心构造失败时恢复 `config.toml.bak`、保持降级并返回可重试 503；核心已恢复而仅后台循环启动失败时不反向回滚有效配置。旧后端或无备份的异常 bootstrap 仍保留 `restart_required` 兼容兜底。

## v0.3.187：初始化与模型配置自救修复（2026-07-29）

- **修复首次初始化与插件模型配置互相锁死**：新装默认配置会带一个启用但尚未填写 Key 的 DeepSeek 占位实例，后端因此以 `llm_registry_unavailable` 降级启动来提供修复界面；此前降级保护层却把设置页自己的 `/api/config/probe-service` 与 `/api/config/discover-models` 也拦成 503，`/setup/` 切到 SenseNova 等新服务商时又把旧占位实例继续作为启用 fallback 提交，最终先出现“获取模型 503”，再以“DeepSeek 缺 API Key”返回 400，插件读取未写入的旧配置后继续显示同一 503。现在降级模式精确放行无写入的草稿探测、模型发现与来源比例建议接口，推荐/画像等业务 API 仍保持 503；首启向导只会停用诊断明确标为 blocking、且未被自定义模块链引用的旧占位实例，并把它从默认链移除（不删除正常实例或改写用户自定义链）。400 改为展示结构化 blocking issue，不再截断整段 JSON；降级保存返回 `restart_required=true` 时向导停在模型步骤，写入不含 Key 的 24 小时续接标记并轮询 liveness，重启成功后自动进入账号连接步骤，避免对旧降级进程直接启动初始化。新增真实降级 FastAPI 回归与 Chromium E2E，覆盖插件/桌面共用探测、SenseNova 替换占位、可诊断 400 和重启续接。

## v0.3.186：移动端 IPv6 与生产稳定性修复（2026-07-29）

- **手机端支持 IPv6 局域网访问（[#130](https://github.com/whiteguo233/OpenBiliClaw/issues/130)）**：默认 `host = "0.0.0.0"` 过去只让 uvicorn 创建 IPv4 socket，IPv6 地址即使存在也没有 listener；`/api/qr-info` 同时只枚举 IPv4，两个二维码生成器直接把含冒号的地址拼进 URL 还会得到非法 authority。现在源码 CLI、Docker 入口和 Windows/macOS 桌面包在默认 wildcard 配置下共用独立的 IPv4 `0.0.0.0` + IPv6 `[::]` listener，避免依赖各系统不一致的 IPv4-mapped IPv6 默认值；IPv6 不可用时 warning 后保留原 IPv4 行为。局域网地址探测继续优先 RFC1918 IPv4，在 IPv4 不可用时回退 ULA / global IPv6 并排除 loopback、link-local、multicast 与 IPv4-mapped 地址；插件和桌面 Web 的二维码统一生成 `http://[IPv6]:port/m/` 合法链接。
- **修复 `/api/events` 并发写偶发 500**：生产进程在 API、账号同步和后台任务同时写行为事件时复用同一个 `check_same_thread=False` SQLite connection；不同线程的隐式事务会互相提交或回滚，最终在 `insert_event()` 的 commit 处出现 `cannot commit - no transaction is active`，实测 13 小时内 24 次。单事件写现在和批量导入一致，使用独立短连接完成 event + `seen_items` + backfill cursor 的原子事务并在结束后关闭；`MemoryManager.propagate_event()` 通过 `asyncio.to_thread` 执行，使最长 30 秒的 SQLite busy wait 不阻塞 API event loop。新增 12 线程、120 次真实 SQLite 并发写回归，修前稳定产生数十到上百个 transaction/API misuse 错误，修后 120/120 成功且两张表数量一致。
- **OpenAI-compatible 明确关闭 reasoning 仍被网关忽略时自动自愈**：商汤 SenseNova 上的 `deepseek-v4-flash` 会在省略 `reasoning_effort` 时沿用模型默认 thinking；关键词 planner 虽显式传 `reasoning_effort=""`，仍可能把 4096 token 全耗在 `reasoning_content`，以 `finish_reason=length/content=""` 结束并退化为兴趣名。为保持 Groq、Together、vLLM 等通用协议兼容，首请求仍不向泛兼容端点强塞非标准字段；只有明确 no-reasoning、且实际观察到 reasoning-only 响应时，才追加一次 `thinking={"type":"disabled"}` 重试。JSON `response_format` 兼容重试顺序保持不变，仍无正文才交给实例链 fallback。
- **修复候选池维护在 299/300 附近无限恢复/裁剪同一批内容**：真实生产库在来源供给变密后稳定出现 `raw 594→638→594` 振荡——维护器为补 1 条 canonical available，先把 44 条 `suppressed` 改回 `fresh`，但这些行要么来自已占满 3 席的 topic、要么排名进不了 topic top-3，实际 available 仍是 299；下一批再压回 47 条，`has_more=True` 又触发同一循环。12.9 小时内因此执行 5624 批、约 27.97 万次无效状态写入并产生 3354 条 ERROR。恢复规划现在直接跳过已满 topic；批量试探后复用 canonical available 扫描做净增长校验，只有与净新增一一对应的可见恢复才保留，其余在同一事务内还原为 `suppressed`。生产库副本由修前四批持续 `594↔638 / has_more=True` 收敛为首批 `594→588`、第二批起 0 mutation / `has_more=False`；新增回归覆盖“满 topic 位移 + viewed 高排名占位 + 低排名恢复不可见”的组合。

## v0.3.185：推荐列表稳定与 X / LLM 链路修复（2026-07-26）

- **修复 Windows 后台不断弹出黑色控制台窗口（`E:\Python\Scripts\rdt.EXE` 等）**：用户群反馈用安装包版本后总有终端窗口自己弹出来，「尤其是设置保存的时候，弹好几次」。根因是 Windows 的进程创建语义：桌面 / 安装包形态的后端本身没有控制台，它每启动一个控制台程序，系统就会**新开一个控制台窗口**，除非显式传 `CREATE_NO_WINDOW`。而后端会自行调用一批外部命令，其中 Reddit 发现走 `rdt` 子进程——保存设置触发一轮补货时，先跑一次 `rdt status --json` 探测，再按关键词逐条跑发现命令，于是窗口一次弹好几个（窗口标题正是 `rdt.exe` 的完整路径）。现在新增 `openbiliclaw/proc.py::no_window_kwargs()`，后端主动发起的全部子进程统一 splat 该 kwarg：`rdt` / `opencli`、自动更新的 `git`、`agent-browser`、mcporter 灵感检索、SQLite 修复的 `sqlite3` / `lsof`、停止托管 ollama 的 `taskkill`。非 Windows 平台返回空 dict，行为逐字不变。用户自己在终端里敲的 `openbiliclaw` CLI 与 `codex login` 不受影响——它们本就该用当前控制台并需要交互提示。新增 AST 全量扫描测试守住该约定，之后任何新增的 spawn 点漏传即测试失败，不必依赖 review 时肉眼盯。**同时堵掉一条孙进程漏网**：`rdt-cli` 自己在凭据超过它的 7 天 TTL 时会 `uv run --with browser-cookie3 …` 拉浏览器 Cookie，这个孙进程由 rdt 而非我们启动，`CREATE_NO_WINDOW` 管不到——而且正因为我们把 rdt 起成了无控制台进程，Windows 反而会给它新开一个窗口，外加 30 秒下包等待。我们的凭据过期判定因此比 rdt 的阈值提前 6 小时（`RDT_CREDENTIAL_TTL_SECONDS`），过期时直接回落插件后端而不再调用 rdt；真机 E2E 顺带抓出 `source_auth/providers.py` 里一份**字面量复制**的同名 TTL（注释明写镜像该常量却不会跟着变），它让 `/api/sources/status` 在后端已放弃 rdt 之后仍绿着报「凭据就绪」6 小时，现已改为读取源头常量并加测试锁定；新增测试比对我们镜像的常量与 `rdt_cli.auth._CREDENTIAL_TTL_SECONDS`，依赖升级改了 TTL 会立刻失败。逐个核过其它来源：X 的 `twitter-cli`、YouTube 的 `yt-dlp` 都是**进程内 Python 库调用**，B 站 / 小红书 / 知乎 / 抖音走插件任务队列，Bangumi 是纯 HTTP，均不起子进程；`twitter_cli.auth` 里同样存在的 `[sys.executable, "-c", …]` Cookie 提取只在 `get_cookies()` / `extract_from_browser()` 路径上，而我们始终显式传入配置里的 cookie 构造 `TwitterClient`，不走那条路。
- **偏好分析无进展看门狗与 20 分钟单请求超时对齐**：阶段 2 的 idle deadline 从 10 分钟延长到 25 分钟，45 分钟绝对上限不变。进展仍只由完整分片结果刷新，心跳不续期；25 分钟覆盖 `[llm].timeout=1200` 的完整单请求窗口，并为两次 65 秒临时 429 cooldown 留出余量，避免 Provider 仍在等待时被外层看门狗提前取消。阶段 4 的 15 / 45 分钟双期限不变。
- **统一兴趣线新增落地主干 live E2E 验证器**：`scripts/verify_unified_line_live.py` 在隔离根上提交两条 dislike 与一条 like，联合核对 `pipeline_layer_update(source=feedback)` 台账增量、旧 `feedback_preference_overwrite` 零增长、一次性迁移 marker、`disliked_topics` 关键词落盘及可选 server log 错误，并以可读 PASS/FAIL 与 JSON 双格式输出；纯 helper 以离线样本单测锁住台账 delta 和优先选择未反馈卡片的规则。
- **单次 LLM 请求默认超时延长到 20 分钟**：全局 `[llm].timeout` 默认值由 300 秒调整为 1200 秒，可配置上限同步由 600 秒放宽到 1200 秒；OpenAI / OpenAI-compatible / DeepSeek / Claude / Gemini / Ollama / OpenRouter 的直接构造默认值、配置 API、桌面 Web、扩展设置页和示例配置保持一致。该值只约束单个 Provider 请求，不改变初始化采集、偏好分析、画像生成或发现阶段各自的整阶段墙钟上限；存量配置中显式填写的合法超时继续保留。
- **Token / reason diet 合并前硬化，撤销不成立的旧回放 PASS**：把 `perf/llm-token-diet` 前向合并到当前 main 后补齐四类安全修复。(1) `CandidateEvalCoordinator` 现在通过 `claim_ready_batch()` 真正执行 `[scheduler].eval_min_batch_size / eval_max_wait_seconds`，并按剩余 coalescing 时间唤醒；手动 CLI 因一次性进程无法保存内存等待，固定 `1 / 0` 立即 drain。(2) embedding 预筛的兴趣可见范围从 compact 可见块扩到与长尾召回一致的 top-256，余弦分数夹到 `0..1`；OpenClaw 和配置 GET/PUT 补齐 `eval_prefilter_mode`，调度 API 同步支持两个 coalescing 字段。(3) `admission_min_score` 支持范围收紧为 `[0.5,1]`，保证 evaluator `score < 0.5 → reason=""` 的候选无法通过任何配置准入；evaluator reason 明确仅为内部诊断，不再声称会经 delight 展示。(4) 重写 `scripts/run_profile_diet_ab.py` 的证据链：精确抽取最近 `evaluated/cached/rejected_low_score` 生产混合样本，不再人为均衡平台/策略；同一冻结快照中交替运行至少 3 组 A/A 与 A/B，两臂共用 source context 与生产 4096 output budget；生产 DB 只读、embedding cache 放临时目录；超时、短向量和解析缺失均直接使门无效；必须输出含 raw paired scores、快照 digest、路由和 usage 的 JSON artifact。此前记录的 21% A/A / 17% A/B PASS 使用了不同 source context、独立快照、回放专用 16K 上限且没有真正执行相对门，因此正式作废，合并前必须用修正脚本重跑。
- **修复 Windows 11 未勾选仍随登录启动、旧启动项无法从设置页识别（[#128](https://github.com/whiteguo233/OpenBiliClaw/issues/128)）**：冻结桌面包此前沿用了源码安装的 `pythonw + openbiliclaw-autostart.pyw` 注册格式，但 HKCU Run 实际先执行的是 `OpenBiliClaw.exe`，PyInstaller 入口又会忽略后面的 `.pyw` 参数；因此脚本丢失时 Windows 仍会启动应用，`is_registered()` 却返回 false，设置页只看 `config.enabled` 便显示未勾选，CLI 的残留清理也被这个假阴性绕过。现在冻结包直接注册 `OpenBiliClaw.exe`，仍兼容识别旧双路径格式；自启动 intent/OS 对账从 CLI 抽成 `runtime.autostart.reconcile()`，由 `openbiliclaw start` 与 `packaging/entry.py` 共用。对存量用户，`enabled=false` 会无条件幂等删除同名 Run 值（即使目标 exe / `.pyw` 已损坏、状态读端报未注册，也不会留下未来重装后复活的项），`enabled=true` 会把旧双路径项原地迁成当前 exe 的直启格式。桌面 Web 与插件 side panel 遇到 `enabled=false + registered=true` 会把开关显示为实际开启并明确提示“残留项”，用户直接关闭即可调用既有事务 API 清理；关闭仍不强杀当前进程，只影响后续登录。测试除 fake `winreg` 外新增永久 `windows-latest` job：构建前用真实 HKCU + 真实 PE 覆盖直注册、缺 `.pyw` 仍启动、损坏项清理和旧项迁移；先用 PyInstaller 输出、再把 Inno Setup 产物静默安装到临时目录并用已安装的真实 `OpenBiliClaw.exe` self-test 跑完“开启注册 → 旧项升级 → 关闭清理”生命周期。真机验收同时发现并修复安装器把带 Git SHA 的展示版本误写入数值型 `VersionInfoProductVersion`、导致 Inno 6.7 拒绝编译的既有问题；手动构建支持 `windows_only` 避开 Intel macOS runner 排队。
- **修复抖音初始化只导入首屏却显示完整成功（[#129](https://github.com/whiteguo233/OpenBiliClaw/issues/129)）**：`bootstrap_profile` 的 API 分页此前把页面偶然发出带 `sec_user_id` 的请求当成唯一身份来源；部分已登录会话始终停留在 `/user/self`，fetch tap 虽安装成功却拿不到 `sec_uid`，四个 scope 只能保留虚拟列表首屏 DOM，最终仍被 dispatcher 无条件提交为 `status=ok`。现在由 MAIN-world bridge 使用页面自身 cookie / 签名上下文请求已由登录探针验证的只读 `/aweme/v1/web/user/profile/self/`：只有 `status_code=0` 且 `user.sec_uid` 非空的正面结果才能成为最终身份并写入同 tab 缓存；URL 编码的 `#RENDER_DATA` 必须同时显式 `isLogin=true` 才能提供观察候选，但仍不能在 profile 探针失败时降级为分页身份，冲突时一律以 `profile/self` 为准。常驻 fetch / XHR tap 不再从被动请求 URL 提取或记录 `sec_user_id`，避免浏览他人主页时把他人公开 ID 送进本机诊断日志。MAIN / isolated 两侧消息 listener 增加同窗口、同源检查以降低跨 frame / 页面噪声误接收（同页脚本仍可发消息，因此这不是授权边界，sentinel / request ID / payload 校验照旧）。拿到权威身份后继续复用既有 cursor / max_time 分页；API 非零业务状态、`has_more` / cursor 类型异常、游标缺失、停滞、成环、触顶和中途 HTTP 失败都不再吞错。若身份仍不可得或分页报错，scope 和任务会以**终态 `degraded`** 完成：已抓到的有效条目继续去重、入 memory，但 `dy_tasks.result_json.status` 与聚合 scope 状态明确标为“不完整”，完成 / 失败终态也不会再被迟到 partial 或重试回调覆盖。CLI 阶段 1 会以 `warning / douyin_degraded` 结束，最终摘要与 API init 均显示“部分完成”（API 保存 `partial_success=true` / `reason=douyin_degraded`），仍让有效事件参与画像建模，不再把 8/8/8/1 这类首屏结果包装成完整成功；6 小时去重不会复用该降级 completed 结果，下一次会重新入队补齐分页。隐私边界同步写入隐私政策与商店说明：只有用户触发 bootstrap 后，页面消息桥才会传经 `profile/self` 确认的公开 `sec_uid`、请求关联字段和解析后的任务条目，不传 Cookie / CSRF token，也不转发未裁剪的原始响应对象。
- **`discover --source xiaohongshu` 的节流文案跟随配置，不再硬写「4 小时」**：把该命令的节流从写死 4 小时改成读 `[sources.xiaohongshu].min_interval_minutes` 时，只改了逻辑没改展示——命令仍打印「节流开关：4 小时节流」和「距离上次关键词生产不足 4 小时」，与实际行为差 80 倍。两处都改为读配置后实测：配置 11 分钟时提示「距离上次关键词生产不足 11 分钟」，`--force` 仍正常绕过。**同一轮真机采样还澄清了两件事**：小红书 producer 在预算放开后严格按 3 分钟出轮（16 分钟 6 轮、每轮 5 个任务、轮间隔 3.0/3.0/3.0/3.0/3.0 分钟），单位由小时改分钟的换算精确无误；而 `[scheduler].trending_refresh_minutes` / `explore_refresh_minutes` **只在候选池不缺货时才生效**——池子低于目标时 `_build_refresh_plan` 会直接返回 source-replenishment 计划，把 B 站四个策略整组下发且不查这两个间隔，实测缺货状态下两个时间戳每 ~1.1 分钟就推进一次。该作用范围已写进 [config 模块文档](modules/config.md)。
- **修复五个来源的节流地板「重启即失效、空跑却锁死」**：抖音 / YouTube / X / 知乎 / Reddit 的 `_is_due()` 依赖进程内的 `_last_run_at`，后端每次重启都会把它清成 `None`，下一个 tick 无视 `min_interval_minutes` 直接开跑。**在真实库里量到了实锤**：Reddit 25 天 517 条命令记录聚成 55 轮运行，其中 5 轮的轮间隔是 8.1 / 9.9 / 11.0 / 35.4 / 39.8 分钟，而当时配置的地板是 60 分钟——只可能来自重启。同一处代码还有个方向相反的毛病：时间戳写在「成功返回」路径上，跑完但零产出的轮次照样烧掉整个周期，而那恰恰是最该立刻重试的情况（刚换好过期 cookie 的来源要白等一整轮）。现在新增共享账本表 `source_producer_runs`（platform / discovered / created_at）与 `Database.record_source_producer_run()` / `source_producer_ran_within()`，八个来源统一以「上一次**真正产出候选**的轮次」为地板：落库因此重启不失效，只记录 `discovered > 0` 因此空跑不烧周期。新增 `runtime/producer_cadence.py` 收口三个helper，未接数据库构造的 producer（单测 / CLI 一次性调用）透明回落到原进程内时间戳，行为不变。真实验证：同一 SQLite 文件上「跑一轮 → 丢弃实例重建（等价重启）→ 再 tick」仍返回 `throttled`（修复前是 `ok`）；零产出轮次后立即再 tick 返回 `ok`。三条回归测试锁住重启存活、空跑不记录、账本读写。B 站 / 小红书 / Bangumi 原本就是查库口径，本次只是并入同一张表的语义描述。**合入后补跑的真机 E2E 又抓出一处漏网**：`ZhihuDiscoveryProducer` 通过 `ZhihuTaskQueue(database)` 间接拿库，自身没有 `database` 字段，于是 `ledger_available()` 恒为假、静默回落到本次要替换掉的进程内时间戳——单测和类型检查都发现不了，只有在活后端里看到「知乎 throttled 但账本无它的行」才暴露。已补字段与接线，并新增 `tests/test_producer_cadence.py` 用参数化断言守住五个 producer 都能够到账本，防止下次再漏。
- **B 站主发现的刷新节奏也并入同一套语义：`trending` / `explore` 由小时改为分钟，同样默认 3 分钟**：上一条把八个来源 producer 的 `min_interval_minutes` 对齐后，B 站仍有一半路径不受它管——主发现（`search` / `related_chain` / `trending` / `explore`）走 `_build_refresh_plan` → `_run_refresh_plan`，由 `[scheduler].trending_refresh_hours = 3` 和 `explore_refresh_hours = 12` 门控，两套时钟单位不同、量级差两个数量级。现在 `[scheduler]` 新增 `trending_refresh_minutes` / `explore_refresh_minutes`（默认均为 `3`），`_is_due()` / `_is_due_soon()` 改为分钟制，桌面 Web 与插件的「调度」页标签由「热门/探索刷新小时」改为「分钟」。**旧配置安全**：`SchedulerConfig(**sched_raw)` 会把整张 scheduler 表 splat 进构造函数，直接改名会让每一份存量 `config.toml` 在加载时抛 TypeError 而无法启动；因此解析阶段先用 `_legacy_hours_to_minutes()` 读出旧键并按 ×60 换算（`3` 小时 → `180` 分钟、`12` 小时 → `720` 分钟），再把旧键从 splat 中剔除——**绝不把 `= 3`（小时）就地重新解读成 3 分钟**，那会让存量用户的 B 站请求量静默涨 60 倍。新键存在时优先于旧键，保存后只写新键、旧键自动消失。真实后端 E2E：用只写旧键的配置启动，API 如实返回 180 / 720，设置页回填 180 / 720，改成 3 / 3 保存后磁盘只剩新键；另加一条回归测试锁住「旧键换算、新键优先、缺省落 3」三种情形。
- **八个来源的 producer 最小间隔统一为 3 分钟，并补齐三个缺失的配置字段**：原先每个来源的补货节奏各自为政且大多不可配——YouTube / X / 知乎 / Reddit / Bangumi 是 `60` 分钟（可配），B 站写死 `30`、抖音写死 `15`、小红书用的是**单位为小时**的 `min_interval_hours = 1`，三者的 `[sources.*]` 段里根本没有对应键。现在 `BilibiliSourceConfig` / `XiaohongshuSourceConfig` / `DouyinSourceConfig` 各补一个 `min_interval_minutes`，小红书从「小时」换算为「分钟」（小时粒度无法表达 1 小时和关闭之间的任何值），八个来源的默认值统一为 `3`。接线一并补全：抖音工厂原本是字面量 `min_interval_minutes=15`、小红书构造时压根没传该参数、`discover-xhs --force` 之外的路径写死 4 小时，现在三处都读配置；config 解析器里另有一套硬编码的 `raw.get("min_interval_minutes", 60)` 兜底会**盖过 dataclass 默认值**，五处一并改为 `5`，并给三个新字段补上解析与序列化——否则写进 `config.toml` 会被静默忽略、保存后不落盘。桌面 Web 与插件设置页的 5 个占位符 `60` 同步改为 `5`。**行为影响**：默认节奏从 15～60 分钟收紧到 3 分钟，对后端直连型来源（抖音 / YouTube / X / Reddit / Bangumi）意味着单位时间请求量上升，插件任务型（B 站 / 小红书 / 知乎）只是入队更勤、后端本身不发请求；单轮规模不变，仍由 `[scheduler].discovery_limit` 与各分支每日预算封顶。**已显式写了 `min_interval_minutes` 的现有 `config.toml` 不受影响**——显式值优先，默认值变更只作用于未写该键的配置。补齐过程中另抓出两处会让新字段静默失效的缺口：`GET /api/config` 的响应构建对这三个来源不传 `min_interval_minutes`，Pydantic 默认值把磁盘真实值盖成 `5`；`PUT /api/config` 的 sources 合并对 B 站只接受 `enabled`、对小红书 / 抖音的数值白名单里没有该键，于是设置页改了也不落盘。两处都已补齐。桌面 Web 与插件 side panel 的「节流」分段同时补上这三个输入框（B 站原本没有节流段，本次新建并注明它只作用于风控兜底路径）。同时用真实 producer 类 + 真实时钟实测了闸门本身：`min_interval_minutes=3` 时连续 11 次每分钟 tick，只在 t+0/3/6/9 分钟开工，其余全部返回 `throttled`——**producer 路径的节流确实生效**。但同一轮排查也澄清了它的作用范围：B 站的主发现、手动「立即补货」和初始化回填三条路径都走 `_run_refresh_plan` / `discovery_engine.discover()`，完全不经过 producer 的 `_is_due()`，因此 `[sources.bilibili].min_interval_minutes` 实际只管「API 搜索被风控冷却时接管的扩展搜索兜底」这一条；非 B 站的 7 个来源则以 producer loop 为唯一稳态路径，配置真实生效。该作用范围已写进 [config 模块文档](modules/config.md)。真实后端 E2E：桌面 5/5、插件 4/4——磁盘 11/12/13 如实回填两端，改成 21/22/23 后正确落盘且未压扁其它来源，显式写了 `60` 的 YouTube 在改动前保持 `60`、显式改 `5` 后落盘 `5`；两端控制台 0 error / 0 warning。
- **平台源设置页改成一个来源一张卡（桌面 Web + 插件）**：原先桌面 Web 的「平台源」tab 把同样 8 个来源摊成 5 段——顶部一排启用下拉、「来源接入状态」8 行、「Cookie / 登录凭据状态」8 个折叠块、中段各平台参数、底部候选池占比——配一个来源要在页面 5 处上下跳，一屏 95 个字段且停用的来源照样全量展示；其中 B 站 / 小红书 / 抖音 / YouTube 四个平台连分区标题都没有，「抖音 Cookie 环境变量」甚至和小红书的三个预算并排在同一行里。现在每个来源收成一张卡：卡面是图标、名称、来源与接入徽章、候选池占比和启用开关，配置默认折叠；展开后 8 个来源共用同一套分段（接入方式 / 发现分支与每日预算 / 节流 / 平台专属 / 验证），差异只体现在段内容——Cookie 粘贴、个人令牌、插件登录态、公开接口四种接入形态共用同一个脱敏容器，分支开关与该分支的每日预算并排成表，没有 `source_modes` 的来源只渲染预算列而不伪造开关。停用的来源只留卡面、不可展开，但配置项仍在 DOM 中参与保存 payload。候选池占比另做一张带权重条形图的总览卡，与卡面数字双向同步。两端同时补吸底保存栏（「已修改 N 项，未保存」+ 就地保存，桌面端另有「放弃修改」按最近一次后端快照回滚）。**四端范围**：移动 Web 没有配置页，CLI 无此界面，故只动桌面 Web 与插件 side panel。所有输入框 id 与 `PUT /api/config` payload 逐字未变，本次只重排信息架构，不新增也不删除任何配置字段——B 站的分支预算、小红书 / 抖音的「最小调度间隔」在后端 `BilibiliSourceConfig` / `XiaohongshuSourceConfig` / `DouyinSourceConfig` 中尚不存在，页面据实说明而非放置空转开关。真实环境 E2E 共 111 条断言、0 个产品缺陷，全程无 mock：用真实旧格式配置（`default_provider` + `[llm.<provider>]` v1 布局）启动，只读不改盘；8 张卡的启用态、知乎分支勾选、Bangumi 条目类型均按 `config.toml` 正确回填，跨 5 种控件改 13 个字段后保存全部正确落盘，未触碰字段做全量扁平化 diff 后零漂移，B 站 Cookie 与 LLM API Key 留空保存均逐字保留；开关改动在保存前显示「保存后生效」pending 徽章，保存后 `/api/sources/status` 的运行时视图立即反映新启用态（无需重启）。真实凭据链路：注入真实商汤 `openai_compatible` 凭据，平台源页保存重写整份 `config.toml` 后再次真实调用 LLM 仍成功（1489ms），密钥逐字未损；注入真实 B 站 Cookie 后 `verify` 走 `live_probe` 真联网返回「已登录 B站」，卡面徽章转 `tone=ready` 并显示「◆ 联网验证」，页面 DOM 与 API 响应均不含 SESSDATA 原文。降级态（缺 API Key，`degraded=true`）下卡片、占比条形图与脏计数仍全部正常。插件保存不会压扁桌面端写入的 `subject_types` / `bilibili.cookie` / `reddit.backend`。浏览器覆盖：Chromium / Firefox / WebKit 三引擎跑桌面 Web，并在真实 Firefox 152 中以临时扩展加载 `dist-firefox` 真机验证 popup 的折叠、sticky 保存栏、脏计数与真实保存落盘；1440 / 1024 / 760px 与插件 420 / 360px 均无横向溢出，控制台 0 error / 0 warning。
- **桌面 Web 开启滚动自动加载后列表不再乱跳、不再重排**：用户群反馈「开启自动加载后总是乱跳，尤其是自动加载消耗库存、后台补货的时候，跳完还会重新排序」。查出两个独立成因，都在桌面 Web：(1) `refresh.pool_updated` 会经 `refreshInitStatus` 拽起一次 `renderAll()`，而 `renderVideos` 是整表 `grid.replaceChildren(...)`——一轮补货连发多次该事件，于是用户正在看的每张卡片被反复销毁重建，浏览器丢掉滚动锚点（跳动）、首屏之外的懒加载封面回落成占位、展开的推荐理由与收藏 / 稍后再看状态复位；同一份代码里 `refreshPlatformAvailability` 明确写着「库存事件不许碰已加载的卡片」，是 init-status 支路从旁边绕了过去。(2) 每次切走标签页都会置 `backendHydrationPending`，切回来必定再水合，而再水合是 `{ replace: true }`——`/api/recommendations` 只返回最新 top 窗口，于是滚动加载出来的卡片被整表覆盖并按后端最新排序重排（这正是「重新排序」）。现在 `renderVideos` 改由 `syncRecommendationCards` 按 recommendation key 增量对账：markup 未变就原样复用 DOM 节点，只增删差集并 `insertBefore` 调序，「列表没变」时一个节点都不动；`refreshInitStatus` 的重绘统一走 `initStatusRenderOptions()`，网格已装真实卡片时只刷新头部 / 库存 / 侧栏；`hydrateFromBackend({ replaceRecommendations })` 默认不替换列表，只有首屏引导和手动刷新才换。**四端范围**：移动 Web 的 pool 事件本来就只合并库存、不重绘卡片，且视图只在首次挂载时加载（切 Tab 保留卡片），扩展 popup 早在 fix 79042ce 已有同类守卫，CLI 无持久列表，故本次只动桌面 Web。真实 chromium E2E 回归：补货事件连发三次后卡片 DOM 节点身份、顺序、数量与滚动位置全部不变（修前卡片身份全丢），切走再切回后本地 34 张卡片仍在且不被后端反序窗口顶掉（修前掉回 24 张并重排）。
- **修复 X（推特）内容发现的搜索恒 404：自建 `x-client-transaction-id`**。本机真实网络排查发现 X discovery 的关键词搜索（`XSearchStrategy` → `XClient.search()` → `SearchTimeline`）在有效 cookie、代理正常、live queryId 也解析成功的情况下仍恒返回 `HTTP 404 + 空 body`，而同一 cookie 同一代理下账号同步的点赞 / 收藏（`likes` / `bookmarks`）全部正常。根因链已实机逐环验证：(1) X 的 `SearchTimeline` 端点**强制要求** `x-client-transaction-id` 请求头，缺失即裸 404，而 bookmarks / likes 端点不要求，所以只有发现搜索受影响；(2) 依赖库 `twitter-cli`（及其 `XClientTransaction`）用**匿名**请求 `https://x.com` 引导该头，但 X 已把 logged-out 主页换成新版 `x-web` 外壳、不再内联 `ondemand.s` 引用，库内 `get_ondemand_file_url` 的正则 `search(...).group(1)` 因此崩在 `'NoneType' object has no attribute 'group'`，transaction 生成器永远初始化不了；(3) `twitter-cli` 0.8.5 已是 PyPI 最新、无更高版可救。修复在 `XClient._client()` 这一 twitter_cli 唯一构建点自建生成器：改用**已登录 cookie** 拉 `https://x.com/home`（仍返回带 `ondemand.s` 的完整 `client-web` 包），构造 `ClientTransaction` 后注入到底层 client 并标记库自身那次注定失败的匿名引导为已尝试，从而跳过它。生成器按 `XClient` 实例缓存一次并复用（对齐 twitter-cli 自身的 1h TTL 模型），复用 twitter-cli 的共享 `curl_cffi` 会话以保持 TLS 指纹与代理一致；引导为 best-effort，任何失败只记 WARNING 并回退到修复前行为（搜索 404），绝不抛错，`likes` / `bookmarks` / `for_you` / `user_tweets` 通路不受影响。真机验证：修复后 `XClient.search("AI")` 返回真实推文，`likes` / `bookmarks` 回归正常；新增 5 个离线单测覆盖种入、缓存、失败缓存不重试与错误吞掉。（详见 [discovery 模块文档](modules/discovery.md)。）
- **LLM 鉴权失败（401）不再重试、不再张冠李戴，并会指名道姓说是哪个 provider**：一位用户反馈初始化卡在「2/4 分析偏好」后报「AI 服务鉴权失败（HTTP 401），API key 可能填错或已失效」，但他的商汤日日新后台「token 却有记录」。排查出三个独立缺陷：(1) 401 此前被映射成通用 `LLMProviderError`，而 `_is_retryable()` 对通用错误返回 `True`，于是每个分片把注定失败的 401 重试满 `_MAX_RETRIES=3` 次——4 个并发分片就在 provider 后台留下 12 条被拒请求，正好制造出「有记录却报鉴权失败」的观感，同时把可操作的报错往后拖了好几轮退避；(2) 用户面文案只说「检查 LLM provider 的 API key」，不指明是哪个 provider / 哪个 endpoint，同时配了主用与备选（或额外 embedding 端点）的用户无从判断该改哪一个；(3) `describe_llm_failure()` / `classify_llm_failure_kind()` 的鉴权判定里含裸子串 `"401"`，而 auth 桶的优先级高于 rate-limited 桶，因此上游 body 里任何一个含 `401` 的 request id / trace id（`req-1401ab`）或 402 余额不足回包，都会被误报成「API key 填错」。现在新增 `LLMAuthError`（携带 `provider_name` / `endpoint`），OpenAI 系（openai / deepseek / ollama / openrouter / openai_compatible）、Claude 与 Gemini 的 `_map_error()` 统一在 401 时抛出它并记 WARNING（含 base_url 与上游 body 摘要），三家的 `_is_retryable()` 都把它视为终态、零重试；文案改为「{provider}（{host}）拒绝了当前 API key」，并显式提示「或是有有效期的临时 token（过期后需重新生成）」——AK/SK 换取的临时 token 中途过期正是「先成功留下用量记录、随后 401」的典型成因。endpoint 一律经 `urlsplit().hostname` 取主机名，base_url 里的内联凭据不会外泄到界面。裸 `"401"` 判定收紧为限定形式（`HTTP 401` / `Error code: 401` / `"code":401` / `status_code=401`，且排除 4010/4011），Gemini 另补 `UNAUTHENTICATED` 与 `API key not valid` 两种非 401 表述。
- **X / Reddit 命令来源统一接入海外网络模式**：X 的 `twitter-cli` 与 Reddit 的 `rdt-cli` / OpenCLI 不再只是碰巧继承宿主进程环境，而是完整遵循 `[network].mode`：默认 `system` 保留环境代理并把 macOS 等系统代理物化给第三方 CLI，`custom` 强制注入指定地址，`direct` 清除子进程代理变量；运行时切换还会重建 twitter-cli 缓存的 curl 会话，避免旧出口残留。浏览器扩展 fallback 的网络所有权不变，仍沿用浏览器设置。

---

## v0.3.184：全端品牌图标统一（2026-07-23）

- **全产品品牌图标统一为新的粉色猫爪标记**：感谢 [@xiongguixg](https://github.com/xiongguixg) 在 [issue #127](https://github.com/whiteguo233/OpenBiliClaw/issues/127) 中主动提供移动端图标方案；项目以选定的方形源图固化 `assets/brand/openbiliclaw-icon.png`，重新派生浏览器扩展 16 / 48 / 128px、PWA / favicon 192 / 512px 与官网图标。side panel、移动 Web、桌面 Web、首次设置页和 GitHub Pages 首页都从旧字母 `B` / CSS 圆环占位切到正式图标。桌面包同时补齐多尺寸 Windows `.ico` 与 macOS `.icns` 并接入 PyInstaller，系统托盘 / 菜单栏也直接加载同一随包 Web 图标，不再单独绘制旧临时标记；社交分享图源同步切换，资产尺寸、桌面容器与各界面引用均有回归测试。
- **弹幕文本加成（P2，补上"视频讲了什么"这一维）**：P1/P3 都在回答"视频**长什么样**"，P2 补正交的另一半——**观众在讨论什么**。B 站候选喂给推荐链路的语义此前只有 `title` + `description`，而 description 常是"求三连"之类的无信息文本、`body_text` 在 B 站路径恒为空，弹幕这个 B 站独有信号只存了计数（`danmaku_count`）、文本被完全浪费。抓取走 `comment.bilibili.com/{cid}.xml`（**无需鉴权、纯 XML**，标准库解析；`cid` 直接从已有的 `/x/web-interface/view` 响应读，**零额外请求**——该响应早就返回了它，只是解析器没读），复用 `BilibiliAPIClient` 的 `trust_env=False` CN 直连与共享限速，**不引入 `bilibili-api-python`**（虽在 pyproject 声明但项目从未使用，引入会带来 Credential 体系 + 两套网络栈并存）。**清洗策略被实测推翻重写**：原计划"按频次聚合，高频弹幕 = 内容共识"，但抓取 BV1LR336sEFX 的 3600 条真实弹幕后发现**恰恰相反**——高频全是社区梗（难说 613×、已取餐 350×、懂你意思 310×、666 9×）、语义价值为零，真正有信息量的是**只出现一次的长弹幕**（"这就是本地AI的优势，除了延迟低，还有绝对的隐私性"、"苹果上市后系统优化导致零售机强于媒体机"）。按频次取 top-N 会精准筛掉全部有用信息、只留噪声。改按长度后又发现刷屏会顶到最前（`保护`×30 = 76 字但只有一个词、重复句、长串标点），于是最终策略是**先压缩重复再按压缩后长度排序**；压缩规则还踩到一个坑：字符 run 压缩会把 "5000电池"/"10000mah" 压成 "500"/"100"，因此数字必须豁免。摘要存 `content_cache.danmaku_text` 新列，**严格不复用 `body_text`**（它渲染到插件/桌面/移动三端卡片正文并进 5 处 LLM prompt，弹幕塞进去会把卡片正文变成一堆"已取餐"）；`danmaku_fetched_at` **空结果也打戳**（否则无弹幕的视频每轮重抓）。嵌入沿用摘要文本本身为键（与其它文本嵌入一致，**不碰 `_mmr_embedding_text`**——它在两处重复实现且要求逐字节一致，改动会让整个 MMR 缓存静默失效）。新 flag `[discovery].danmaku_enabled` / `danmaku_fetch_limit` / `danmaku_max_chars`（默认关；**纯文本信号，无需多模态嵌入模型**，与 P1/P3 不同），关闭时加成恒 0、排序逐字节一致。至此 `serve()` 上共四路独立信号并行叠加。
- **视频关键帧加成（P3，深度整合视觉信号第二步）**：封面是 UP 主手选的营销图、常常标题党，**不代表视频内容**——P1 用封面建的口味质心因此天然受限。P3 改用**真实视频画面**匹配同一套质心。原方案假设关键帧要"下载视频段 + ffmpeg 抽帧"（高成本，本排在最后），**实测推翻了这个假设**：B 站早已为每个视频预生成关键帧雪碧图（拖进度条时的预览缩略图），`GET https://api.bilibili.com/x/player/videoshot`（**无需 cookie / 无需 WBI 签名**）即可拿到——实测一张 61KB 雪碧图 = 100 帧，**不下载视频、不需要 ffmpeg**，成本与抓一张封面同级，比弹幕方案还便宜。30 个真实视频抽样（5 分区、时长 45s–5106s、含 2009 年老视频）**覆盖率 100%**，平均 277 帧/视频。两个只有实测才会发现的坑已处理：①长视频返回**多张**雪碧图（最多 11 张 = 1100 帧），采样必须跨全部雪碧图全局分布，只取 `image[0]` 会让长视频只覆盖开头；②单帧尺寸**不固定**（160×90 与 480×270 并存），必须从响应读 `img_x_size`/`img_y_size` 而非硬编码。帧向量 **max-pool** 后对 P1 质心算有界加成 − 惩罚，独立常量 `_KEYFRAME_*`（**未标定**，铁律 3），在 `serve()` 上与封面↔文本锚点、P1 视觉画像**三路并行叠加**。新 flag `[discovery].keyframe_enabled` / `keyframe_max_frames` / `keyframe_fetch_limit`（默认关，需叠加 `multimodal_enabled`），关闭时加成恒 0、排序逐字节一致。雪碧图下载复用 `runtime/image_cache`（`hdslb.com` 已在白名单且 CN 直连，铁律 1），预热挂 `prewarm_pool_mmr_embeddings` 并对**空结果也打时间戳**（否则无 videoshot 数据的视频每轮重抓）。
- **修复 P1 遗留的后台任务失控**：`_maybe_rebuild_visual_profile` 此前探测 `hasattr(registry, "create_task")`，但 `BackgroundTaskRegistry` 的方法叫 **`track`**（无 `create_task`），因此该分支恒为 False、静默回退到裸 `loop.create_task` —— 视觉画像重建任务不被注册表跟踪，热重载时 `RuntimeContext.cancel_all()` 无法取消它，旧运行时的任务会残留到新运行时。改用 `track` 并加回归测试（同时断言注册表确实没有 `create_task` 方法，防止再次猜错 API）。
- **用户视觉画像加成（P1，深度整合视觉信号第一步）**：在已有「封面↔文本兴趣锚点」跨模态加成之外，新增**独立并行**的视觉信号——把用户**点赞/踩过**的推荐封面聚成 k 个均值质心（`recommendation/visual_profile.py` 贪心凝聚，复用 `_normalize_topic_keys` 骨架但用均值质心表达多峰口味），候选封面↔质心同模态余弦映射为**有界加成（正向）− 有界惩罚（负向，即"标题党封面"降权，仅扣本信号、不跌破 0）**，独立常量 `_VISUAL_PROFILE_*`（floor/ceil 0.55/0.80，与跨模态 0.15/0.45 不同，**未标定**，换真实模型后按分布重标，铁律 3）。质心存新表 `user_visual_clusters`（主库，profile-scoped），由 `rebuild_visual_profile()` 在 `precompute_delight_scores` 同 tick **节流重建**（仅当 `recommendations.feedback_at` 比上次 `updated_at` 新才跑），`serve()` 热路径只读内存 + URL-keyed 封面缓存、零 API 零聚类；公平门同 `_VISUAL_COVER_MIN_COVERAGE`。新 flag `[discovery].visual_profile_enabled`（默认关，需叠加 `[llm.embedding].multimodal_enabled`），关闭/无反馈时加成恒 0、排序逐字节一致。**A/B 结论**：合成 fixture 下 +画像 vs 仅封面加成 nDCG 增量为 0（同向冗余，预期——合成数据里文本锚点与用户质心同向），真实增量需回放真实库验证（`scripts/ab_visual_bonus.py` 的 P1 variant + `data/ab_visual_bonus_report.json` 的 `visual_profile` 段）。

---

## v0.3.183：多实例模型路由与真实模型发现（2026-07-23）

- **模型配置从“Provider 名 + 一个迷惑的备选项”升级为可编排的端点实例路由**：新增 `[llm.instances.<id>]`，每个实例独立保存 Provider 类型、Base URL、token、模型与协议选项，同类型渠道可同时存在；`default_chain` 支持任意长度、可拖拽排序的全局故障切换，Soul / Discovery / Recommendation / Evaluation 默认继承，也能各自配置严格不越界的实例链。Registry 改为实例 ID 注册与实例级 cooldown，响应和探针返回实际命中的 `instance_id`，初始化前置检查会沿完整链寻找可用端点。桌面设置页提供实例卡片、编辑器、链条排序与逐实例 / 整链真实测试；插件也可新建、编辑、删除和逐实例测试，使用窄屏友好的上移 / 下移维护全局默认链，并完整回传 PC Web 创建的模块链（模块链编辑仍留在 PC Web）。两端保存其他设置都不会再把新路由压回旧格式，密钥输入留空时保留已保存值。旧 `default_provider` / `fallback_provider`、Provider 分段和模块 model override 会无损投影，只有新版 UI 保存时才迁移；仅含样例默认模型、没有凭据且未被引用的远程模板分段不会误迁移成实例。安装器、CLI、setup、Docker 模板与配置 API 均保留全部实例和顺序。Embedding 本轮仍保持独立配置，避免 chat 切换时悄悄改变向量空间。

- **模型名可从当前渠道真实拉取，同时始终保留手填**：桌面 Web、插件实例编辑器和 `/setup/` 新增「获取模型」，把当前未保存的实例草稿提交到无写入的 `POST /api/config/discover-models`，后端使用该实例自己的 Base URL / token 调用 OpenAI 兼容 `GET /models`，排序去重后填入可编辑下拉框；失败不会清空用户已输入的模型名，加载期间按钮禁用并用 `aria-live` 就近反馈。OpenAI 协议没有“列出某模型支持哪些 reasoning effort”的标准接口，因此 Effort 下拉仅是按 Provider / 模型给出的本地建议，仍允许任意手填；泛 OpenAI-compatible 仅在新版实例中明确填写非空 Effort 时透传，旧格式升级继续保持“不发送”语义，避免安装新包后请求体静默变化。

- **模型路由迁移可以真实回退旧版本**：首次把已有旧 `config.toml` 写成 v2 前，中心保存层会创建逐字节、同权限且永不覆盖的 `config.toml.pre-llm-routing.bak`；只读、旧格式保存、新建 v2 和后续 v2 保存均不误建备份。新增 `openbiliclaw config-export-legacy [--output PATH] [--force]`，在不改当前配置的前提下生成 `0600` 的旧 schema 副本，并用回读校验后才原子替换目标；输出逐项披露旧格式无法表达的同类型端点折叠、全局长链截断、模块 fallback 截断和端点重绑定，Embedding 保持独立不变。自动测试冻结上一代解析契约；本次验收另用真实上一版源码解析导出文件，避免“当前版本自己能读”冒充降级兼容。

---

## v0.3.182：账号同步、换批去重与桌面升级交接（2026-07-21）

- **统一兴趣更新线默认开启（2026-07-28 门禁证据）**：真实 LLM A/B 三道门配套 `--aa-control`；相同输入、无 retraction 的 A/A 两轮出现 6 个无关新增兴趣（`Rust 编程`、`并发编程`、`异步编程`、`源码分析`、`科技/AI`、`科技（AI/数码）`），且 `raised_weights` 为空，证明旧门 3 的全局冻结在量 LLM run-to-run 噪声。门 3 因而改为撤回目标定向门：只拒绝匹配撤回 topic/title 的新增 dislike 与匹配兴趣涨权，无关兴趣漂移明确不归此门；默认 `scheduler.unified_interest_line=true`，`false` 仍是旧反馈批的逐字节回退。A/A 任一轮没有成功返回 `PreferenceAnalyzer` 结果仍显式失败。另补齐 `_render_config_toml` 遗漏的五个 `profile_consolidation_*` 字段及 round-trip 契约，并让 OpenClaw bootstrap 与 API runtime 一样向 `SoulEngine` 透传 canonical database 和 `satisfaction_filter_enabled`。

- **统一兴趣更新线 Wave B：反馈批退役成 shim + 旧游标一次性迁移（默认仍关）**：`process_feedback_batch_if_needed()` 变成一层薄 shim，方法名不变 → `FeedbackBatchScheduler`、CLI 反馈命令、OpenClaw 适配三个调用方零改动。`unified_interest_line=false` 逐字节走旧批线（回退路径，Wave A 的 `TestFeedbackBatchContract` 6 条契约继续钉着它）；`true` 时先做一次幂等迁移——旧游标 `last_processed_feedback_event_id` 之后未消费的 feedback 事件逐条经 `signal_from_feedback` 还原成 `FEEDBACK` 信号入线（**不能用 `signals_from_events`**：它永远不产 FEEDBACK 类型，迁移行会静默丢掉优先级消费、dislike 归档、门控重建与 `source=feedback` 台账全部特权），再触发 `pipeline.tick()` 让已达阈值的 INTEREST 缓冲立即消费。迁移**跳过 retraction**（旧批线本就排除，且它们早已在写入当时抵消过对应正向行，此刻补折价只是对着没有那些正向行的偏好层重放噪声；「排除→折价」只对将来的实时信号生效）。**顺序是先落游标+标记、后入线**：两者同处 `feedback_state.json` 一次原子写入，结构上不存在「标记有、游标没推进」的半截态；剩余崩溃窗口最多丢掉未迁移的尾巴（有界，行仍在事件账本里），而反向顺序会在每次崩溃重启把真实反馈重新计入偏好层（无界重复）。幂等标记 `unified_interest_line_migrated_at` 是必需的——游标挡不住迁移后落账的实时行被二次入线。台账写点 `feedback_preference_overwrite` 在开关开时停写（只停写不删读，`openbiliclaw ledger` 仍能查历史行），接班的是 `pipeline_layer_update(source="feedback")`。同时新增 `scripts/run_unified_interest_ab.py`：同一组真实反馈跑旧批线与统一线各一次（各自 `copytree` 隔离项目根，源根只读），输出三道门（新增 dislike 超集 / top-10 兴趣名 Jaccard ≥ 0.8 / 注入合成 retraction 后无新增 dislike 且无权重上调）的观测值与机器可读 JSON；门不过不翻默认值。

- **修复 `scheduler.feedback_batch_threshold` 永远不落盘**：`_render_config_toml` 从来不 emit 这一行，插件与桌面设置页的「反馈分析积累阈值」输入框是只写不读——任何一次设置保存都会把用户调过的值静默复位成默认 3。统一兴趣更新线复用同一个键做优先级消费阈值，静默复位会连带静默改掉兴趣层节奏。顺带补齐三面构造点：`cli._build_soul_engine` 与 OpenClaw bootstrap 此前根本不传 `feedback_batch_threshold`（永远用硬编码默认 3），也没接 `unified_interest_line`（Wave A 只接了 `api/runtime_context.py`），现在 API / CLI / OpenClaw 三面读同一份 config。

- **统一兴趣更新线 Wave A：反馈接进认知流水线（默认关，暗发）**（用户决策 2026-07-27「兴趣更新合成一条路」）：兴趣层长期有两条事件驱动写入路径——认知流水线快线，和 2026-03-09 遗留的反馈批（独立游标 + 阈值 3 + 另一次全量 `analyze_events`）。两套触发状态、两套 retraction 处理、两套台账写点，每次改兴趣语义都要问一遍「另一条线要不要同步」。Wave A 先把管道铺好：① 新增回退开关 `scheduler.unified_interest_line`（bool，**默认 false**）；② 打开后 `/api/feedback` 把反馈作为 `FEEDBACK` 信号喂进 `ProfileUpdatePipeline`（`signal_from_feedback` 此前零调用者），且含反馈的 INTEREST 缓冲攒够 `feedback_batch_threshold`（默认 3，值不变、只是计数点从游标搬到缓冲）即**绕过 600s 最短间隔立即消费**；③ 消费侧 `_update_interest` 继承批线全部特权：新增 dislike 归档（归档非删除）、显著变化 → 接入点③门控重建（trigger 仍是 `feedback_batch`、写点仍是 `feedback_soul_rebuild`，旧批与新线共用同一实现）、批后 held-replay、台账 `pipeline_layer_update` 记 `source="feedback"`。**默认关是硬要求**：旧批线仍在跑，两条同时开会把同一条反馈算两次；关闭时 `/api/feedback` 与缓冲判定逐字节回到今天。旧批线现有语义（阈值触发、retraction 排除但推进游标、dislike 归档、门控重建 shadow/enforce 两态、held-replay、游标幂等）已由 `TestFeedbackBatchContract` 6 条特征测试钉死作为合并验收契约，每条突变各打红。retraction 从「整条排除」改为走 pipeline 既有折价是**有意变更**，需 Wave B 的真实 LLM A/B 门证明不放大 dislike 后才退役旧线。

- **对话直通深层：你亲口说的话当轮改画像**（用户决策 2026-07-27）：对话线在兴趣层本来就是快速通道（≥0.8 当轮直写），但深层有两个窟窿——①过门的 goal/value/state 候选只喂一次偏好 prompt 就标 applied 消失，**不会成为重建输入**；②重建只在「偏好显著变化」时触发，「我最近其实处于转型期」这类不动兴趣权重的纯深层自述当轮进不了深层。现在：过门的深层自述落成 `validated=True / user_verdict="confirmed"` 的假设（**用户第一人称自述就是确认**，经 `merge_insights` 去重、重复陈述强化同一行而非复制），并强制当轮门控重建（`dialogue_soul_rebuild`），新写点 `dialogue_deep_selfstatement` 入台账与写点清单。interest/dislike 快线行为不变，态势门控两道（接入点①逐候选 + 接入点③重建）照过。

- **深层画像获得受控自主权：行为佐证足够久的假设可以不等用户点头**（用户决策 2026-07-27）：此前深层唯一入口是「用户确认的假设 → 门控重建」，模型形成的判断哪怕被行为反复佐证也只能一直躺在待聊列表里。现在增加第二扇门——**行为挣来的自主资格**：假设满足置信度 ≥0.8（高于用户确认路径的 0.75；真实认知产出的新假设落在 0.5-0.75，只有跨周期反复佐证才到 0.8+）、创建 ≥7 天（一个下午的热情不能改深层）、证据 ≥3 条、且用户**从未裁决过**，即可进入与用户确认完全相同的状态机：同一台去抖（6h）、同一道态势门控、同一套台账与重试上界，台账 refs 以 `auto_hypothesis:` 前缀区分。首次入队会原子记录已消费 ref，避免同一假设在清标后每轮重复重建；门控上下文会收到自动假设正文而非只见哈希。带时区的 `created_at` 会与本地时钟对齐后计算资历。**用户拒绝过的假设永久丧失自主资格**（`user_verdict="rejected"` 一票否决，置信度 0.99 也不行）。另设**高置信快速档**（用户决策）：置信度 ≥0.95 免掉 7 天资历等待——真实认知产出里 0.8+ 已需跨周期反复佐证，0.95 意味着模型在多轮佐证下几乎确定；证据下限、未被裁决要求、态势门控、拒绝否决全部照旧，快速档只买到速度。Codex review 补了三处：时区 `created_at` 与 naive `now()` 相减崩溃、同一自主假设清标后每 6h 重复重建（持久化一次性消费集）、门控此前看不到自主假设正文（`auto_validated_hypotheses` 入门控上下文）。

- **第一条更新线不再在真空里解读事件**：快线的兴趣更新此前只看「这批事件 + 现有偏好」——三条木工视频到底是新兴趣还是旧兴趣回潮，模型没有任何判断依据。现在 `_update_interest` 把近期认知尾巴（觉察/洞察各取 5 条，与 portrait 重生成同窗口）作为 `<recent_awareness>` / `<active_insights>` 段传入偏好分析 prompt（顺序稳定→易变，保 provider 缓存前缀）；init 分片与反馈批**刻意不传**（init 尚无认知、反馈按其字面判断），不传时 prompt 与旧版**逐字节一致**（回放不变性有测试钉死）。真实 LLM 新旧对照：头部 8 个兴趣两版完全一致（无回退），差异仅在尾部——带语境版把 Rust 系列事件归并进既有兴趣而非另立门户，并多识别出几个小众兴趣。

- **重跑 init 不再把同一次行为记两遍**：`init` 结尾用 `propagate_events` 把这一轮抓到的账号快照全量写进事件账本，重跑时会把同一份快照再写一遍。真实实测（同一个库跑两次 init）：**账本 56% 是重复行**，699 个键连观看时间戳都完全相同——那不是二次观看，是同一次行为被记了两次，凭事件条数算出来的权重全部虚高。现在导入前按 `(事件类型, 内容身份, 时间戳)` 与账本已有行比对跳过：键里带时间戳正是为了让真实的重看仍能落地（重看 `view_at` 不同、重新收藏 `fav_time` 不同）；认不出身份的行一律保留——**认不出 ≠ 重复**，宁可留着也不能默默丢信号；读账本失败则什么都不丢，原样导入。跳过条数会打在 init 输出里。

- **看完了算证据，划走不算把柄**：`classify_event_satisfaction` 的规则表里从来没有 `view`——扩展看视频发的是带 dwell 的 `click`（会判定），B 站历史发的是 `view`（恒为 `unknown/fallback`）。后果落在相关链深挖上：种子优先级里「判为满意的观看」是 60、普通观看是 10，而历史全是未判定，于是**你真正看完的视频和随手点开三秒划走的排同一档**。现在 `view` 走一条**只判正向**的规则：完播 ≥80% 且观看 ≥15 秒 → `positive/finished_watch`，其余一律保持 `unknown`。低完播刻意不判负——自动播放、误点、看预告、重看时进度重置全长这样，把它们变成负面样本会污染 `negative_exemplars` 并直接影响内容评估。阈值有校准依据：真实 500 条历史（461 条带完播数据）完播率**中位数只有 0.071**、32% 不到 5 秒，≥0.8 命中 7%（30 条），≥0.7 是 11%、≥0.5 是 19% 都太松；0.8 同时与 `ProfileBuilder._history_weight` 既有的「看完」界线一致，全代码库一个定义。实测影响面：687 条事件里判为 positive 的从 187 增至 217，相关链种子分布从 `{100:187, 10:500}` 变为 `{100:187, 60:30, 10:470}`——只有那 30 条真看完的升档，其余一条不动。

- **一个收藏就是一个信号：收藏终于进得了初始画像**：收藏在事件账本、偏好分析（`analyze_events` 分片里各占一条）和增量管线（`favorite` 属 `ENGAGEMENT_EVENT`，强于 `BEHAVIOR_EVENT`）里一直是独立事件，唯独初始画像不是——`combined_history` 把 200 条收藏打包成一个 `[收藏夹汇总]` 行，而里面那份 `_favorites` 列表**写进去了却没有任何代码读它**：`_summarize_history` 只取 `_favorites_summary` 那一句「共 200 个收藏，涵盖: 默认收藏夹」。也就是说用户主动存下来的东西——最强的意图信号——是画像里唯一一个标题都看不见的部分。现在每条收藏与观看并排成行进入 `combined_history`，带 `event_type="favorite"`（这个字段承重：它同时决定强信号权重 3.0、采样里 40% 的预留份额、以及语境渲染成「收藏了」而非「看了」）；`_history_timestamp` 补读 `fav_time`——收藏没有 `view_at`，读不到就等于没时间戳、会整体掉出时间分层。收藏夹名字仍保留一行汇总（「AI Agent」「学习」这类用户自建标签没有任何单条字段携带）。真实数据 A/B（500 观看 + 200 收藏）：采样 100 条里收藏从 **0 条变成 60 条**；真实 init 对照下画像从泛泛的「想弄明白」收敛到「从论文到代码一路追下去」，洞察出现「强化学习在 LLM 中的应用」这类只存在于收藏里的判断，兴趣条目从 128 降到 87（更聚焦、不再重复），生活面（游戏 / 动漫 / 美食 / 健康养生）未丢失。

- **收藏拉回来的内容不再被扔掉**：收藏夹接口一次就返回 `intro` / `cover` / `cnt_info` / `pubtime` / `attr` 等完整条目，而 init 只留了标题、UP、收藏夹三个字段。实测 200 条真实收藏样本：① **13 条（6%）是失效视频**，标题字面就是「已失效视频」，原样进了画像 prompt 和事件账本——等于告诉分析器「这人对已失效视频感兴趣」；现在按 `attr` 判定（无 `attr` 时按标题兜底）在 init 与 account sync 两侧一并丢弃。② 播放量（中位 6.5 万，18% 不足 1 万）与发布时间写进事件 metadata——这是「爱挖冷门还是追热门」的唯一判据。③ 简介入库（截 200 字、跳过占位符 `-`）但**刻意不进画像 prompt**：154/200 有内容、中位 105 字，但相当一部分是充电 / 大会员这类恰饭文案，喂进去等于每次 init 多花约 2 万字符去稀释真实兴趣信号。注：收藏接口不返回分区 / tag，要补得逐条再请求一次视频详情（200 次请求），不划算——所以历史有分区、收藏没有。

- **老收藏自愈进去重账本**：只有「新增」收藏才会变成事件，所以装 OpenBiliClaw 之前收藏的、以及旧版本收藏事件没带身份的那批，靠事件永远补不回来——实测老库倒回重扫后 `seen_items` 一条没涨。现在 account sync 每 6 小时拿到完整收藏快照后，用新增的 `Database.mark_items_seen()` 直接把 bvid 写进去重账本：幂等、不产生事件（不会重复计入偏好信号）、冲突时保留既有真实事件的溯源。真实老库实测一轮同步 `seen_items` 500 → 575，收藏快照 53 条全部覆盖。同时把 account sync 每文件夹的收藏上限从 50 抬到与总预算一致的 500（与 init 同口径，`max_total_items` 已兜住请求量）——50 的时候一个 800 条的默认收藏夹有 750 条永远进不了去重。

- **收藏过的内容不再被推回来 + 初始化的觉察/洞察进长期库**：两处都是「init 产出没被下游认领」。① **去重**：stage 1 的历史一直是入库的（stage 1 结尾 `propagate_events`），但收藏事件既没有 `bvid` 也没有 url——没有身份就进不了 `seen_items`，用户明确收藏过的视频照样会被当新内容推荐；而且 `seen_items` 只从 `view` 事件派生，`favorite` / `like` / `coin` 一概不算。现在收藏事件带上 `bvid` / url / `fav_time`，硬去重来源同时纳入 `favorite` / `like` / `coin`（`follow` 不算——关注 UP 不等于看过这条内容），并给回填游标加了类型集版本号：老库游标停在最新事件上，不倒回就永远扫不到新纳入的类型，升级时会自动倒回重扫一次。历史事件也补上了 `content_id` / 完播秒数 / 时长 / 分区：`watch_seconds` / `video_duration_seconds` 在偏好分析的 compact 白名单里，模型据此区分满播与 3 秒划走；`ProfileBuilder` 的抽样权重也读它。（**注意**：这不改变 `inferred_satisfaction`——`view` 根本不在 `classify_event_satisfaction` 的规则表里，只有显式正向 / `feedback` / `click` / 被动浏览四类会被判定，扩展看视频发的是带 dwell 的 `click`，B 站历史发的是 `view`，后者恒为 `unknown/fallback`。）② **认知**：init 的觉察/洞察此前只活在内存的 `_init_cognition_context`，塑造完首份画像即丢弃，于是全新装机跑完 init 后「待聊确认」是空的——系统刚形成了具体猜测却一条都不问。现在它们经与常规认知同一条 merge 路径落库（去重、生命周期、用户判断语义一致），觉察引用本轮事件并**如实标注 `approximate`**（模型按轮归属而非按条），落库记 `init_cognition_persist` 台账。落库后 `build_initial_profile` 会同时看到持久化副本和内存副本，因此按归一化文本去重再喂画像构建——否则同一条觉察被当成两条独立观察加倍计权。

- **初始化的觉察/洞察草稿不再被最近的分片吃光**：偏好分析按 `events[i:i+200]` 分片，而拉取顺序是最新在前；`_merge_init_cognition_contexts` 旧实现按分片顺序遍历、到 cap（觉察 12 / 洞察 8）就 `break`，于是最近的一两个分片占满全部名额，更早时期的觉察和洞察一条都进不了画像。构造三个时期各 200 条事件实测：最近期贡献 6 条觉察、中期 6 条，**最早期 0 条**；洞察同理 4/4/**0**。现改为**从时间两端交替轮转**（最新片、最早片、次新片、次早片…），每轮各取一条、去重后进入下一轮，产出少的分片自然退出后续轮次而不浪费名额，cap 与去重语义均不变。两端交替是真机 A/B 逼出来的：纯轮转在单元测试（分片均衡）下通过，但真实历史的分片数量极不均衡——420 条刷屏占了总事件的 75%，也就占了约 75% 的分片，洞察只有 8 个名额，从最新分片顺序轮转前八片全是刷屏，实测**洞察里的木工/摄影反而从各 1 条掉到 0 条，比修复前更差**；改为两端交替后恢复为摄影 2 / 木工 1。同场景修复后为每个时期各 4 条觉察、洞察也覆盖三期。这与同批修复的 `ProfileBuilder` 历史抽样是同一类问题：按到达顺序取用，等于让最近的行为独占对「这个人是谁」的解释权。

- **初始化的历史抽样从「取前 N 条」改为「代表性 + 时间铺开」**：`ProfileBuilder._summarize_history` 此前按到达顺序切 `titles[:100]` / `contexts[:100]` / `recent|older[:50]`，而真实拉取顺序是最新在前——1000 条历史里模型只看得到最近约 100 条，更早的长期兴趣无论用户互动多强都进不了画像。实测生产数据：旧办法从 800 条里选出的 100 条只覆盖**最近 0.6 天**（全量跨度 5.2 天），且把全量中唯一一条收藏漏掉了。现在抽样与增量链路同源：权重复用满意度语义（明确互动 > 高完播 > 一般 > 划走，划走不归零），先留 40% 预算无条件收下收藏/点赞/投币这类明确互动——否则集中在某一时段的互动会被其他时间桶的配额挤掉，与「疑惑被高置信假设埋掉」是同一类问题——余额再按 6 个时间桶均摊，薄桶剩余回流给最有代表性的行为。同一份抽样同时驱动 titles / contexts / recent / older，不再是三个互不相干的前缀；`count` 仍报真实总量并附 `sampling_hint` 告知模型这是抽样。缺时间戳（过半）时退回到达顺序。同数据集实测：时间覆盖 0.6 天 → 4.8 天，事件类型从 view 97 + follow 3 变成 view 80 + click 12 + scroll 4 + follow 3 + favorite 1。**未改动**：偏好分片 `events[i:i+200]` 与 init 觉察/洞察本就全量覆盖，截断只存在于画像构建这一处；prompt token 预算维持不变（仍是约 100 行）。

- **用户对假设的否定不再被后续分析抹掉**：`InsightHypothesis` 新增 `user_verdict`（`""` / `confirmed` / `rejected`），与 `validated` 分工——后者是「当作真的用」，前者记录「用户是否表过态」。此前 `merge_insights` 一律取 `max(旧, 新)`，而 reject 只把置信度压到 ≤0.35、不留任何被否定过的痕迹；于是下一轮 12h 洞察提炼若再次产出同一条假设并打 0.8，`max(0.35, 0.8)` 直接把用户的「不准」抹平，该假设还会重新越过待聊阈值（0.60）去问用户一件已经否定过的事。同时置信度只增不减：一条假设一旦被打过高分就永久高位，无论后续行为怎么变。现在按 verdict 分流——`rejected` 取 `min`（能继续走低但不能被谈回去）、`confirmed` 取 `max`（一次弱分析不能打回确认下限）、未评价则双向跟随最新分析值。对话中由用户自己给出措辞的修正版假设同样记 `confirmed`。旧数据缺字段按「从未评价」加载，反序列化对取值做白名单。**深层准入未动**：仍是 `validated AND confidence >= 0.75` 的与门，事件给不了 `validated`，「事件自动下沉深层」依然不成立。

- **真机浏览器 E2E 与对话结算的实时刷新修复**：用 Playwright + 真实 `serve-api` + 真实 LLM 跑通桌面对话面三场景——待聊列表里疑惑确实占到保留席位（真实 UI 显示「有点疑惑 50%」，把 0.74 的假设挤掉）、修正式结算渲染成 `revised`「已按你的修正记下」而非「已标记不准」、以及对锚已失效的卡片点「准」时卡片保持 `pending` 且不弹「已确认这条猜测」。过程中挖出并修掉一个既有 UX 缺口：**对话内结算落库在回复完成之后**（worker 还要跑归属判断与队列 job），而桌面端只在回复完成那一刻刷新一次确认面，导致用户说完「我认可修正版」后卡片一直停在「正在聊这条」，必须手动刷新才更新。现在回复完成后按 1/2/5/5/5/5/5 秒继续重读，直到卡片终态或用完 ~30 秒预算（对齐卡片 action 的 `CARD_ACTION_POLL_DEADLINE_MS`），且只在屏幕上确有未结算卡片时才轮询——首版 8 秒预算在真机实测中被证明会漏。

- **认知链路遗留项收口（四修 + 三验）**：① **revise 不再谎称"已标记不准"**——修正式结算此前投影成 `rejected`，用户刚说"我认可修正版"却看到否定；新增 `revised` 终态（文案「已按你的修正记下」）贯穿 `card_settlements` 投影、锚释放白名单、状态机允许转换与两端 toast。② **疑惑在待聊列表拿到保留席位**——假设的 confidence 是"我多有把握这是真的"（越高越该问），疑惑的 interpretation_confidence 是"我对猜测多有把握"且低置信才配当疑惑，两者语义相反却混在同一降序里排，叠加 top-3 截断与真实画像里 334 条 ≥0.60 假设（截断线 0.76），疑惑永远出不来；现固定预留 1 席，空席回落给假设。③ **觉察证据链升级到事件粒度**——prompt 要求每条 note 给出 `source_event_ids`，解析侧校验其必须是本批事件 id 的子集，越界即整条降级回整批并 WARNING；真实 LLM 实测 4/4 精确归属、零编造，`approximate` 标记从"恒为真"变成"仅在无有效归属时为真"。④ **防双计确认已是硬不变量**（非本次新增）：candidates 侧 Jaccard 过滤 + settles 侧"有活跃锚即整批不处理"，两道都有测试且突变有效；同时实测记录其边界——它是词面防线（同义改写 0.06 分漏过），bge-m3 语义兜底经校准后**主动放弃**（重复类 0.599–0.796 vs 旁支类 0.446–0.569，分隔带仅 0.03，误伤代价高于漏放）。验证侧补齐：认知循环的真实调度链路（refresh `_loop_soul_pipeline` → `pipeline.tick()` → `cognition_cycle.run_if_due()`，开 scheduler 真机跑通、觉察自动产出且证据链精确）、移动 Web 只读契约（此前**无任何测试守护**，现补四条守卫，防止第二个结算入口回潮）、Docker 形态下的配置回滚容错（独立 runtime root + 模板 + ollama 播种链路实测 4/4）。

- **事件→画像链路两处修复（真机端到端验收发现）**：① **增量 pipeline 的新兴趣跳过 trial 试用期**——`apply_evidence` 只接在 `analyze_events` / `learn_from_dialogue` / 反馈批三处，而日常浏览走的 `ProfileUpdatePipeline → layer_updaters._update_interest` 漏接，产出的 interest 没有 `state`；`get_state()` 把无 state 读成 `active`，于是随手看几个视频产生的新话题直接以活跃身份参与推荐权重，12h `scan_lifecycle` 也不补（无 state 的 interest 在它看来已是 active）。现在增量路径同样计一次证据并落 `topic_lifecycle` 台账（`source="pipeline"`）。② **空池永久饿死画像更新**——画像流水线归 `soul.*` = MAINTENANCE，`RefillAdmissionSemaphore` 在库存 EMPTY 时无条件 park 它给补货让路；当补货永远不来（source 全关、凭据失效、网络不通）就是无限期停摆，全程只有启动时一行 `Background LLM work gate blocked`，实测 `POST /api/events` 10 分钟不返回而 LLM 直连 1.1 秒正常。新增 5 分钟 `MAINTENANCE_STARVATION_GRACE_SECONDS` 饥饿上限：到点放行并 WARNING 指出补货可能失败，库存恢复后豁免立即撤销并重新武装；补货正常到来时的预留语义完全不变。两处均补回归测试并实测突变有效，真机复验 7/7（空池 `POST /api/events` 324 秒返回 200，兴趣落 `trial`/`evidence_count=1`）。

- **对话确认入口三处诚实化修复（真机验收发现）**：① 锚被另一张卡片占用时，卡片 action 后端本就诚实返回 `stale_anchor`/`anchor_dependency_failed` 且零写入，但共享前端 `responseCardState()` 因 `"stale"` 不在 `CARD_STATES` 里而回落到**乐观终态**，卡片渲染成「已确认」、桌面/popup 还各弹一条成功提示，用户这次确认静默丢失、刷新后打回待确认；现改为归入既有 `retryable_error` 路径回滚乐观态并给诚实文案，两端 toast 同步。② deprecated `POST /api/insights/feedback` 把同一种锚拒绝包装成 `200 {"ok":true,"matched":false}`，老客户端无从得知失败也不会重试；现返回 `409` + 可诊断 detail，`Deprecation`/`Link` 头与 `202 processing`、正常 `200` 路径均不变。③ `config.toml` 出现未知 key 会让 `SchedulerConfig(**sched_raw)` 抛裸 `TypeError` 直接拖垮 `load_config()`（升级写入新字段后回滚旧版本即复现，本仓一个残留 worktree 配置就曾连带 74 个用例变红）；新增 `_filter_dataclass_kwargs` 覆盖 scheduler / storage / logging 与全部 8 个 provider section，未知 key 过滤后按 `[section].key` 逐条 WARNING，已知字段值不受影响。三处均补真实回归测试并实测突变有效（删除修复后对应用例必红）。

- **对话结算单队列 Wave 0 护栏与 RED 契约**：冻结 spec §2.2 的 12 项入口、10 类 protected mutator 与 pending-open 三类 raw sink，确定性复现文件 fence、future-anchor、建锚 reservation/failed-head/同 ref owner/commit-point 及当前入口旁路；新增实际 worker Task + lifecycle nonce 双校验 guard primitive，child task 不能继承写权限，旧 worker 迟到 cleanup 不能撤销新 permit。此 Wave 不接线现有 runtime façade，也不改 force_tick、exploration、pipeline、OpenClaw 或 CLI writer。
- **对话结算单队列 Wave 1 Task 1.1（typed queue + admission registry）**：`dialogue_learn_queue.py` 原地泛化为 11-kind typed envelope、单 consumer 与 completion Future；admission 同步完成深拷贝、sequence、exhaustive anchor transition、owner reservation、snapshot 和入队。`AnchorAdmissionRegistry` 支持 persisted/reserved/failed/absent/not-applicable、同 ref 多 reservation/最新 head、owner-only 单次 resolve、failed head 原子前移与旧引用 GC；六类 terminal 在 mutator 返回后的下一次 await/effect 前转正。queue worker 已绑定实际 Task permit，reentry 与 child-task mutation fail closed，reload 可先 exact revoke old 再注册 new，旧 finally 只能 compare-and-clear。runtime 统一 dispatcher、显式 legacy-direct 与 LLM 在线内串行留在 Task 1.2–1.3，本项未做 Wave 2 receipt/schema/锁栈改造或 Wave 3 endpoint cutover。
- **对话结算单队列 Wave 1 Task 1.2（runtime dispatcher + 显式学习模式）**：API runtime 的旧 learn-only adapter/attribute 已收敛为唯一 `DialogueSettlementQueue` 与 typed dispatcher，`SocraticDialogue(mode=queued)` 同步提交 `learn`，缺 queue 在 LLM 前显式失败，测试可选 `reply_only_test` 零学习；CLI/OpenClaw 仅在两个 allow-listed 构造点显式 pin `legacy_direct`，仍各 direct learn 一次且 queue submit=0。热重载按 pause/drain old → exact revoke old → start/register new → publish new 交接，失败 rollback 用 fresh nonce 恢复 old，旧迟到 `finally` 不能清掉 new permit；其余 typed kind 在 Wave 3 cutover 前 fail closed，未改命令/adapter response、force_tick、exploration 或 pipeline。
- **对话结算单队列 Wave 1 Task 1.3（LLM 在线内串行）**：runtime dispatcher 的 callable 边界改为 `DialogueDispatcher`/`DialogueJob` 强类型；`learn` 在 background-admission bypass 内由唯一 worker 直接 await `learn_from_dialogue`，不 detached、不进 task registry、不包 whole-job timeout，provider 自身有限 timeout 保持不变。确定性双 job 用例固定 LLM `max_active=1`，首项阻塞 0.5 秒时 heartbeat ≥10，force_tick/exploration/OpenClaw 调用均为 0，并核对每项 `queue_wait_ms/run_ms`。生产仅记录 `202 ratio >1%` / p95 `>5s` 的后续拆分观察阈值，不抽 DTO、不加 digest/CAS、不预埋第二队列。
- **对话结算单队列 Wave 2 Task 2.1（轻量 winner receipt）**：`card_settlements` table rebuild 收敛为 `ref/verdict/turn_id/payload/applied/result/event_id/created_at/updated_at`，最早表与旧 claim/segment 表升级均保留 winner，旧 event/applied 进度映射到稳定 event identity；删除数据库级进程锁、文件锁、5 分钟 lease、claim token 与三段 CAS DAO。event INSERT 与 receipt 标记同事务且可重放，completion 无 token；50 路同 ref 仍只有一个 immutable winner。本项不做 Wave 3 endpoint cutover。
- **对话结算单队列 Wave 2 Task 2.2（worker-only apply + future-anchor 删除）**：`SoulEngine` 的 direct `settle_*` executor 拆成公开 `submit_*` admission façade 与实际 worker Task 才能进入的 `_apply_*`；锚 relation 和普通 chat settles 在当前 learn worker 内直接 apply，不递归提交自己。每个 job 只消费受理时冻结的 persisted/absent/failed snapshot，彻底删除 generation 0 在执行期读取 current anchor 的补抓；非预约未来锚与旧 generation 均在 receipt 前零副作用退出。旧文件 fence XFAIL 改成 worker await 期间 heartbeat 继续的无锁 GREEN，用真实 queue 顺序证明 nested settle 不增加 queue depth。生产 card/legacy/anchor-builder/confusion endpoint cutover 仍严格留在 Wave 3。
- **对话结算单队列 Wave 2 Task 2.3（七段 crash gap + stable effect）**：worker 固定执行 event → object → derived → rebuild marker → durable `applied=1` → projection → exact-generation anchor release，并在七个边界提供精确故障注入；confirm/revise/confusion 共 12 个参数化代表 case 加 runtime 重建用例证明显式同 ref retry 始终采用原 winner，event/object/derived/marker/解锚语义各至多一次。`applied=1` 现在先于 frozen-anchor 校验进入 publication-only 分支，只补 audit observer、跨 session projection 与精确解锚，不重做对象语义。`profile_update_ledger` 新增可选固定 hash `effect_key` 与 partial unique index；结算主台账和 revise-derived 台账首次写失败不阻断 applied，恢复后显式 retry 可补齐且 `INSERT OR IGNORE` 不重复。锚 admission snapshot 补齐 kind 字段，confusion 不再被默认解释成 hypothesis。单 turn GET 已按 F7 只 submit `card.reconcile`：首次可返回 pending，worker 补齐跨 session publication 后再次 GET 见终态，request direct write=0、object/derived/rebuild 计数仍为 1。除此之外未接线 card action/legacy/锚 builder/疑惑等 Wave 3 production 入口，也未增加自动 scanner。
- **对话结算单队列 Wave 3 Task 3.1（cards/open/reconcile/legacy cutover）**：卡片 confirm/reject/discuss/defer、待聊 open 的 confusion schedule/retarget/rollback 与 anchor establish、列表/单 turn GET reconcile、deprecated legacy feedback 全部改为 typed submit；endpoint task 不再直接写对象、卡片、疑惑或锚。discuss admission 在入队前建立 owner reservation，worker 内完成 `pending→discussing→anchor`，失败补偿回 pending；同 ref 重复 builder 各自 resolve。card/legacy 只 shield 等待 1 秒，空队列保持原 200，队头阻塞才返回 202 processing，已入队 Future 不被 HTTP timeout 取消。
- **对话结算单队列 Wave 3 Task 3.2（chat/anchor/probe/replay cutover）**：普通 chat 的 speculation/insight/confusion settles 与锚 relation 保持当前 learn worker 内 apply；probe/confusion durable reply 只提交 typed side-effect job。弱正向 probe classifier 在 worker 恰执行一次，返回 immutable exploration intent 后由原 producer task 在 permit 外交给既有 exploration 路径。12h cognition hook 只枚举并提交专属 `confusion.attribution.replay`，不借 `learn`/`confusion.reply.apply`，重复 identity 复用现有 receipt；`force_tick`、pipeline、direct probe button、avoidance 与 exploration writer 边界未扩大。
- **对话结算单队列 Wave 3 Task 3.3（按需 202 与客户端轮询）**：popup 与桌面 Web 的共享卡片 helper 仅在 action 返回 `202 processing` 时按 1/2/5 秒（随后 5 秒）读取 durable turn，总截止 30 秒；终态立即停止，deadline/读取失败/页面 abort 转本地 `retryable_error`，不改 durable pending。同步 200 与 opposite `already_settled` 仍直接采用权威结果。进程重启可丢失内存 job，用户重试同 action 后按 immutable winner/receipt 三分支恢复；没有新增 durable job 表。移动 Web 无卡片 action UI，CLI/OpenClaw 显式 `legacy_direct`，均未加轮询。
- **对话结算单队列 Wave 3 Task 3.4（护栏、旧栈删除与交付）**：Wave 0 的 10 组 production wiring strict XFAIL 全部改为 worker-only GREEN，并增加 production AST/raw-sink、inherited child、热重载 permit 与 100 次 declared-entry 交错守卫；仓库不再保留 strict XFAIL。删除 card settlement claim/lease/文件锁/三段 CAS/恢复 scanner，以及 discussion `attempt_token/discussing_at` CAS/scanner；fresh runtime schema 只保留轻量 winner receipt，旧列仅迁移读取。旧 takeover/fencing、stale lease 与旧 schema 测试按原业务意图改写为 serialized winner、stable-effect retry、orphan discussion reconcile 与 migration-only 证明；CLI/OpenClaw compatibility allowlist 仍精确两处。
- **对话结算单队列第二轮验收修复 F1/new-3**：除 owner resolve 外，non-builder release/relation completion 也不能把全局 `_latest_head_key` 从更晚受理的跨 ref reservation 拉回旧 ref；真实 worker barrier 固定“旧 A 已释放、新 B reservation 已受理、A completion 刷新”后 latest 仍是 B，并保持 failed-head 前移语义。
- **对话结算单队列第二轮验收重构 F2/new-1/new-2**：删除 worker-lineage inline dispatcher 与 delegated-task 临时授权；actual worker 内的普通 chat/锚嵌套结算只沿当前 task 调用栈直调 `_apply_*`。`submit()`/`submit_and_wait()` 对 actual worker、任意层 active child 和跨 job detached stale child 都立即报 reentry；guard 始终只认 actual worker task + lifecycle nonce，旧 child 在下一 job 运行时仍无写权。
- **对话结算单队列第三轮验收修复 F2**：dispatch 完成刷新不再只依赖 payload target，而会从 effective frozen snapshot 或 builder transition 推导实际受影响 ref；因此 targetless `learn` 内的 support/contradict/revise/confusion answer 直调，以及 confusion replay builder follow-up 内的解锚，都会在 completion 前把 registry 刷成 durable absent/新 generation。刷新继续携带原 job sequence，同 ref 与跨 ref 的更晚 reservation 都不会被旧 completion 覆盖。
- **对话结算单队列独立验收修复 F3**：confirmed/rejected 卡片收到迟到 defer 时返回 `already_settled` 与真实终态，不再伪报 deferred，也不创建或延长该 ref 的确认 cooldown。
- **对话结算单队列第二轮验收修复 F4/M3**：orphan recovery 增加 30 秒 claim-age fence，并继续在同一 UPDATE 内校验 ask-turn identity 与 `NOT EXISTS(chat_turns)`；双连接固定 recovery 插入 claim→create 窗口时返回 `released=False`、creator 随后成功建 live turn，真正超龄无 turn 的 crash claim 仍可恢复。官方测试分别以零年龄强制命中 live-turn fence，以及用不存在的旧 ask-turn 尝试回收当前 claim；删除 `NOT EXISTS` 或破坏 `ask_turn_id = expected_ask_turn_id` 任一条件都必红。
- **对话结算单队列第二轮验收加强 M7**：官方动态 expiry probe 让同一 detached child 在父 job 内先触发 reentry、跨到下一 job 再尝试 protected mutation 与 submit，旧 child 两条路径均被拒；静态契约同时要求 inline/delegated 授权符号为零，临时授权 reset mutation 无藏身路径。
- **对话结算单队列独立验收修复 F5**：删除“单一 settlement + fake dispatcher”的 100 路自证，改由安装在真实 `create_app` runtime 的 dispatcher 跑完 11 个 typed kind，并在同一总括用例固定跨 ref head 与 worker-child reentry 两个 blocker 交错；十类 protected category 逐类调用各自生产 mutator/handler，核对 SQLite、锚、卡片、疑惑与 cooldown 均零旁路写。
- **对话结算单队列独立验收修复 F6**：`anchor.establish` admission 仅接受三类已声明 producer source，任意其他非空来源也 fail closed；计划内三处 legacy-column 静态门改为递归路径 glob，实际排除 migration-only `storage/database.py` 后命中数从 6 归零。

- **移动端惊喜卡恢复整卡点击打开（issue #126）**：用户反馈手机上「惊喜推荐一定要点『看看』才能跳转，下面的内容却是整卡点一下就进去」，问这是防误触还是别的设计原因。查下来两者都不是——这是实现不一致而非有意为之：移动 Web 的普通卡片在 `renderCard()` 里绑了整卡 click（动作行 `stopPropagation` 排除按钮），而惊喜卡的 `.delight-tray` 上只有左右滑动切卡的 pointer handler，位移不足 50px 时松手什么也不做，于是卡体在视觉上像可点、实际是死区。现在死区内松手（位移 <10px，`DELIGHT_DRAG_DEAD_ZONE`，与桌面端 `_DELIGHT_DRAG_DEAD_ZONE` 同值）等同点击「看看」：走同一个 `handleDelightAction(d, "view")`，因此已读标记、`POST /api/delight/respond` 与打开链接的语义和按钮完全一致，不是另一条旁路。10px–50px 之间仍然刻意不触发任何动作，手指轻微拖动不会误开内容；≥50px 继续是切卡。反馈按钮、聊天输入框等交互元素本就在 `pointerdown` 阶段 `stopPropagation`，天然不会被整卡点击吸收；已反馈完成（`show_actions=false`）、聊天 composer 展开中、或拿不到内容 URL 时不接管点击，避免抢走「点空白处收起输入框」的预期。**四端范围**：桌面 Web 的普通推荐卡本来就只有动作按钮、没有整卡点击，惊喜卡另有封面点击区 + 「去看看」按钮，自身已经自洽，本次不动以免改变桌面既有手感；插件 popup / side panel 没有惊喜卡（delight 只走 background 通知），CLI 无此交互。新增三个 Playwright 真机级 E2E：点击卡体打开内容并只上报一次 `view`、拖动 30px 不打开也不上报、点「喜欢」不会连带打开内容。

- **认知画像流水线(四 Wave)+ 深层线归一合入**(2026-07-22,分支 `feat/cognitive-profile-pipeline`,codex 共十轮对抗 review、59 findings 闭环;规格见 `docs/plans/2026-07-17-cognitive-profile-pipeline-{spec,plan}.md` 与 `docs/plans/2026-07-22-deep-line-consolidation-spec.md`;真实环境 E2E 7/7。三线更新、觉察/疑惑/假设生命周期、态势门控(默认 shadow)、统一台账 `openbiliclaw ledger`、topic 生命周期状态机、深层影响收敛为「假设确认→门控下 soul 重建」。细分条目如下)。
- **第二轮独立验收：结算原子边界与入口收口**：claim takeover 与对象/派生/marker/台账现共用跨连接临界区，每个非 SQLite 副作用前锁内重读 token 并把 segment CAS 留在同一边界，真实 lease 接管只执行一次对象；LLM 返回后的首个 anchor 副作用改为锁内 generation CAS 并强制消费，失配整批 WARNING+台账，赢家 payload 固化代号且旧收据不能释放同 ref 新锚；无锚 chat 的 speculation/insight/confusion settles 删除直写旁路，与卡片、legacy、锚统一进入 ref 仲裁并返回 `already_settled`。新增三组真实线程/异步屏障测试及对应 fencing/CAS/旁路 mutation 复验。
- **对话确认入口 Wave A（Task 0–3）**：durable turn 新增兼容 `payload`、稳定 `(created_at,rowid)` 顺序与创建时固定本地时间；单学习队列捕获持久化锚的 `ref/generation` 快照，既有 insight extraction 追加 kind×relation 白名单矩阵，无锚 prompt 保持逐字节不变。confusion 结算所有权从 API completion 迁到串行锚处理器：classifier 输出先入 FIFO `replay_queue`（≤5、精确队头 fencing、四类解锚清队列并记 dropped），副作用失败留队且后续轮不能越过；12h cognition cycle 重放已存输出并幂等补扫 completed crash gap。卡片 action/API 属后续 Wave，本轮只落 `card_settlements` 仲裁基础。
- **对话确认入口 Wave B Task 4（durable 卡片 action）**：`scope="hypothesis"` 结构卡片创建即 completed，worker 不触发 LLM；`POST /api/chat/cards/{turn_id}/action` 支持 confirm/reject/discuss/defer。confirm/reject 用 ref 主键仲裁、5 分钟可接管 claim token、三段 flags 与 fencing；event 段在同一 SQLite 事务完成占位+INSERT，投影只认 `applied=1` 并批量刷新跨 session 卡片。discuss 以 `discussing_at+attempt_token` CAS 后建锚，失败回滚、超时读取清 token 拒旧请求。单一 history 回灌所有 session 的 completed chat/hypothesis/confusion；UI 列表仍按 session 过滤。legacy insight feedback 保留响应契约、标记 deprecated 并以 `source=legacy_endpoint` 转发同一路径。
- **对话锚结算统一仲裁（验收修复）**：`support/contradict/revise/answer` 不再从 `SoulEngine` 直写对象或直接 patch origin 卡片；卡片 action、legacy endpoint 与锚处理器统一进入 ref 级 `INSERT OR IGNORE → claim token → event/object/marker fencing → applied → 全 session 投影`。仲裁行新增赢家 payload，接管者只续做原赢家的 revise/answer 语义；冲突时画像、收据与各端卡片只呈现同一 applied verdict。
- **锚 generation 二次校验（验收修复）**：串行学习任务在 LLM 返回并完成本地解析后、任何对象/`replay_queue`/候选/画像副作用前，再从持久化单锚原子读取并同时比较 ref+generation；期间被同 ref 新 generation 顶替的迟到输出整批丢弃，WARNING 并追加 `anchor_stale_generation_drop` 台账。入 LLM 前已 stale 的排队快照也直接丢弃，不再退化成无锚提取。
- **门控解析错误分流（验收修复）**：`PostureGate` 的非法 JSON、非 object JSON、缺失/越界 verdict 与无 registry 统一按 provider 异常处理；enforce 仍保守 downgrade，但明确 `is_error=True` 让 `rebuild_pending` 保留重试，shadow 记 `shadow_error`。只有模型明确返回白名单内 accept/downgrade/reject 才是 `is_error=False` 的真实判定。
- **疑惑 resolve/pop 崩溃恢复（验收修复）**：12h 兜底扫描先选取**任意状态**下非空的 `confusions.replay_queue`，再补扫 clarifying completed-turn gap；因此 resolve 已提交、FIFO 队头尚未 pop 即崩溃时，重启仍会幂等重放 terminal 对象并精确出队。共同对象结算存在未 applied 收据时同步续做其 claim/fencing；defer 的 open 状态恢复不再重复累加 `defer_count`。
- **结算台账严格 best-effort（验收修复）**：`card_settlements.seg_marker` 先在独立事务按 claim token 提交，`settle_insight/settle_confusion` 台账随后用另一事务尝试追加；ledger INSERT 失败只 WARNING，不能回滚 marker、阻断 `applied=1` 或改变 API/锚结算结果。
- **rebuild marker 写盘失败显式化（验收修复）**：marker 采用同目录临时文件写入、`flush+fsync` 后原子替换，任何序列化或文件系统失败都会 WARNING 并向结算调用方抛出，且在 `finally` 清理 `.tmp`。失败收据停在 `seg_marker=0/applied=0`，不发布卡片投影；后续 claim 接管可从该段安全续做，避免出现“结算已发布但永无 rebuild_pending”。
- **rebuild marker 触发幂等（验收修复）**：已有 pending 再收到相同 trigger ref 时不再重写文件或重置 `set_at/retry_count`，因此重复结算不能延长 6h debounce、也不能清零错误重试次数；只有首次出现的新 trigger ref 才合并来源并按「新证据重开」重新置时、重置 retry。
- **学习队列 drain 超时阻断热重载（验收修复）**：`DialogueLearnQueue._join()` 超时在 WARNING 后重新抛出；`RuntimeContext` 在旧队列 drain 失败时恢复其接单并让配置事务感知失败，不执行 `cancel_all`、不构造/安装新 generation。只有 drain 成功才 swap，彻底关闭新旧学习 worker 并发窗口；shutdown 超时仍在 `finally` 强制取消 worker。
- **关键并发/代次测试去假绿（验收修复）**：锚 stale 用例保持 ref 不变、只推进 generation，删除 generation 比较即失败；`rebuild_pending` 错误分支改由真实非法 JSON 穿过 `PostureGate` 解析器，不再直接伪造 `is_error=True`。confusion resolve→pop 故障后关闭并重开数据库验证恢复；卡片旧执行者用线程屏障暂停，在新 token 接管但尚未写段时恢复，实测 event/object/marker/applied 四次写全部被 fencing。
- **对话确认入口 Wave B Task 5（待聊与双轨冷却）**：新增 `GET /api/chat/pending-confirmations`（高优先级最多 3 条，支持 `count_only=1`）与 `POST .../{ref}/open`。用户主动 open 不受时间冷却，`(ref,session)` 在 `BEGIN IMMEDIATE` 内查重，同端复用、跨端各自产卡/提问并以 `pending_open` 建锚；疑惑仍受 `clarifying <= 1` 硬约束。系统抛出同时满足全局 12h 与同对象 72h，状态持久化；只在非空、非幂等重放的 durable 用户消息处理内先 INSERT 确认 turn 再 INSERT 用户 turn，`attached_to_turn_id` + `(created_at,rowid)` 保证故障恢复不重附且顺序确定。此 Task 仅交付后端 badge/count 数据，SW/桌面消费属于 Task 6，未提前实现。
- **对话确认入口 Wave C Task 6（卡片 / 待聊 / 角标前端）**：popup 与桌面 Web 共用 `web/shared/dialogue-confirmation.js`，在 durable 对话流中渲染假设卡片四动作、依据展开、已结算态、疑惑纯提问和无 payload 文字降级；confirm/reject/discuss/defer 先乐观更新，失败回滚，跨窗口 `already_settled` 以服务端 verdict 校正。两端都提供待聊列表与主动 open，分别固定 `session="popup"` / `"webui"`，桌面侧栏同步显示待聊计数。service worker 复用既有 30 秒 alarm 拉 `?count_only=1`，runtime-stream 刷新去抖，离线 / 未初始化抑制数字，健康 / 错误角标继续优先。移动 Web 本 Wave 仅把 active insights 改为只读展示并引导到插件或桌面端对话确认；移动端卡片仍按规格留待跟进版，Wave D 的 CLI / 其余旧确认入口迁移未提前实施。
- **对话确认入口 Wave D Task 7（只读收尾）**：新增 `openbiliclaw questions`，按配置端口只读调用 `GET /api/chat/pending-confirmations`，直接复用服务端过滤/排序/上限且不提供结算动作。popup 与桌面 Web 的画像/认知更新区移除旧「准 / 不准」按钮、handler 和 `submitInsightFeedback` 客户端写入口；连同移动 Web，active insights 三处均只读，主动假设确认只留在 durable 对话卡片。deprecated `POST /api/insights/feedback` 后端转发兼容仍保留。模块/API/CLI/扩展文档与 `docs/architecture.md`、`docs/spec.md`、README 中英文顶部架构图同步增加「确认入口」节点。
- **GateDecision 错误分类（F7）**：`soul/posture_gate.py` 的 `GateDecision` 新增 `is_error` 字段。enforce 下 LLM/解析**异常**仍保守 downgrade，但 `is_error=True`，让重建调用方对「真实 downgrade 判定」（清标）与「瞬时错误」（保留 pending 重试）分流；与 shadow 侧 `shadow_error`/`shadow_downgrade` 语义对齐；接入点①调用方不消费该字段，行为不变。
- **P1 退役 + 一次性迁移**：pipeline 不再消费 VALUES/CORE——`_BUFFERED_LAYERS` 摘除、`FEEDBACK` 只路由 interest+surface、对话 `value/state` kind 在 pipeline 内失活、`update_layer(VALUES|CORE)` 封死 no-op + WARNING（接入点②随之退役）。新增 `migrate_pipeline_deep_buffers`：构造时幂等地把持久化 buffer 里残留的 VALUES/CORE 信号确定性转成 awareness note（前缀 `[migration:pipeline-deep]`，内容 hash 去重，marker + 台账行 `pipeline_deep_migration`，清空旧键）。
- **接入点③快照泛化 + P2 补门控**：`_gate_soul_rebuild` 泛化承载三触发源（dialogue / feedback_batch / confirmed_hypotheses），快照带 `trigger`/`write_point`/旧 soul 摘要/触发上下文，返回 `GateDecision`。反馈批显著变化的整份重建此前绕过所有门控，现接入接入点③（`feedback_soul_rebuild` 写点，enforce 可拦）。
- **重建输入过滤 + rebuild_pending 状态机**：所有 soul 重建只纳入 `validated && confidence>=0.75` 的假设（`_rebuild_active_insights`），rejected/未验证假设不可见。`update_from_feedback` 单点：confirm/reject 均置持久化 `rebuild_pending {set_at, trigger_refs, retry_count}`；去抖 6h 后由 12h 认知循环 / 下次对话学习 / 反馈批触发门控重建（`hypotheses_soul_rebuild` 写点）。清标语义：accept 清 / 真实 refusal 清+记 `last_gate_refusal` / error 保留有界重试（`is_error` 区分，retry<2）；`set_at` compare-and-swap 对账并发 re-mark；重启自动恢复。
- **文档**：`docs/modules/soul.md` 新增「深层影响唯一模式」小节 + 台账写点表与态势门控/更新器行更新；架构图无跨模块布线变化（深层节点注记）。
- **topic 生命周期状态机（Task 8）**：新增 `soul/topic_lifecycle.py`，给 interest（flat `InterestTag` 与 Onion `InterestDomain` 两层）叠加 `state ∈ {trial|active|decaying|archived}` + `evidence_count` + `last_evidence_at` + `parent_topic`。序列化点（`soul/profile.py`）**只在非默认时 emit 这些键**——默认 `active` 的 topic 与旧数据序列化**逐字节不变**，旧数据缺字段兼容默认 `active`。跃迁（常量带首轮校准注释）：新 topic 首见→`trial`；证据 ≥5 或持续 ≥7 天→`active`（`apply_evidence` 接在 `analyze_events`/`learn_from_dialogue`/反馈批偏好写 chokepoint）；`last_evidence_at` 静默 ≥30 天→`decaying`（权重×0.5），再 ≥30 天→`archived`（不删）；`archived`/`decaying` 遇新证据→直接复燃 `active`。衰减扫描 `scan_lifecycle` 并入 12h `ProfileConsolidator`（缺 `last_evidence_at` 的旧 topic 永不被扫，启用不团灭）。**dislike 改「归档+避雷」**（`archive_topics` 置 `archived` 保留台账不删）；**细分提议**（子类占父域权重 ≥60%）只经 `topic_subdivision_proposal` 记台账不执行（shadow）。所有跃迁进 `profile_update_ledger`（`topic_lifecycle` 写点）。
- **最小消费 + 开关（Task 8）**：新增 `[soul].topic_lifecycle_serialization`（`off`|`on`，默认 `off`）。`off` 时 `build_profile_summary` 与未接状态机前**字节不变**（回放门）；`on` 时把 `archived` topic 排出 LLM 可见画像（domain/tag 两级）。进程启动由 `create_app`/CLI 读入设 `discovery.strategies._utils.set_topic_lifecycle_serialization`；trial 小流量推荐消费仍 out of scope。
- **觉察提炼节奏（Task 9）**：`cognition_cycle` 除 12h 兜底外新增**提前触发**——未提炼事件 ≥30 条 或 强信号事件（`comment/danmaku/reply` 带文本 / `feedback` / `inferred_satisfaction∈{positive,negative}`）→ 本 tick 立即跑觉察。新增**单飞锁**（`run_if_due` 见锁被持有即跳过，due-check + watermark 消费全在锁内，重叠 tick / 提前触发抢跑恰一执行）；state JSON 写入 **tmp+fsync+`os.replace`** 原子化；异常/取消时 watermark 不前进（下轮重做）。同批事件 awareness prompt 输出路径不变（回放不变性）。
- **架构图**：Wave D 只在 `soul/` 内部加状态字段与触发节奏，无新模块 / 跨模块布线 / 数据流变化，架构图（Wave C 已含态势门控节点）无需改动。
- **态势门控 builder + 执行体（Task 6）**：新增 `llm/prompts.py::build_posture_gate_prompt`（静态 system——三判定 accept/downgrade/reject + 「冲突不是错误是新假设」+ `sort_keys`，入 invariance 清单）与 `soul/posture_gate.py::PostureGate`。三模式：`off`=完全旁路（门控 LLM 零调用、逐字节等价）；`shadow`(默认)=commit boundary 捕获不可变快照 `{before,after,source_refs,gate_id}`，异步旁路任务只消费快照（判定前活状态再写入不污染，带断言）、**零延迟不阻塞原写入**、判定落台账 `shadow_accept/downgrade/reject`、LLM 异常落 `shadow_error`；`enforce`=同步判定，异常/解析失败/非白名单 verdict 保守 downgrade。新 caller `soul.posture_gate` 注册 usage recorder。
- **配置 + save-time 校验（Task 6）**：`[soul].posture_gate_mode`（默认 `shadow`）与 `posture_gate_force_enforce`（默认 `false`）。切到 `enforce` 需 save-time 三条件——最早有效 shadow 判定距今 ≥14 天 **且** 近 14 天有效判定 ≥10 条 **且** 近 7 天 ≥1 条，否则 blocking 拒绝（`Database.posture_gate_shadow_stats` 供数，config PUT 处理器接线）；`posture_gate_force_enforce=true` 无条件放行（有风险，文档注明）。
- **三接入点接线（Task 7）**：①对话候选按 kind 分流——interest/dislike 现路径，goal/value/state 过门控（reject 丢弃、downgrade 置信×0.6 转 insight）；②管线 VALUES/CORE 层 updater 写入前过门控（downgrade 固定置信 0.5 转 insight、层写入回滚；ROLE 不过门控）；③soul 整份重建 diff 过门控（downgrade/reject→放弃本次 rebuild + 台账）。`off` 模式 `learn_from_dialogue` + pipeline 与现状逐字节一致（回放门）。
- **Wave B 遗留接线**：**held 重放消费端**——`ConfusionManager.pending_replays` + `SoulEngine.replay_held_updates`：resolved 真实兴趣型的 replaying held 项作为证据并入下一次偏好分析（rebase 语义，不直接写权重），成功后回调 `mark_replay_applied`（幂等）；引擎构造时 `recover_replaying` 处理上一次会话崩溃残留（置 `applied_unverified` 不重复提交），反馈批后运行消费端。**代理行为折价**——疑惑 `proxy_behavior` 出口对 `evidence_refs` 关联事件调 `Database.discount_events_by_confusion`（盖 `discounted_by_confusion` + 强度折至 0.2，`real_interest` 不折）。
- **疑惑对象 + 两产生源（Task 4）**：新增 `confusions` 表（partial unique index `WHERE status='clarifying'` 跨连接原子保证 clarifying ≤1，即打扰预算）与 `soul/confusion.py`（`ConfusionManager` 状态机 `open→clarifying→resolved|dismissed|expired`）。疑惑不写画像，只驱动下游澄清与冻结。产生源一=觉察：新增独立 builder `build_awareness_with_confusions_prompt`（静态 system，入 invariance 清单）+ `analyze_with_confusions()`；既有 `analyze()`/`build_awareness_prompt` 一字不动，`cognition_cycle` 切新 API 属**有意行为变更**（新旧输出 A/B 语义等价，过质量铁律）。产生源二=推测僵局：`SpeculatorTickResult.stalemate`=expire 时 `0<confirmation_count<threshold`（现存字段判定），pipeline 转疑惑。TTL 扫描并入 12h `cognition_cycle`。
- **澄清三路 + 三出口 + 冻结（Task 5）**：**ask** 走 durable chat `scope="confusion"`（`schedule_ask` claim + 72h 冷却持久化于 `asked_at`，重启不复问；`defer` 复用探针忽略语义），**wait** 14 天 TTL，**probe** 复用现有探针域。三出口 `resolve()`：`real_interest`→held 重放 / `proxy_behavior`→丢弃 / `dismissed`；durable `scope="confusion"` 侧效应按情绪判断结算（单一所有权，不走 settles）。**冻结** `apply_confusion_freeze` 在对话偏好写 chokepoint 拦截——冻结 topic 的新增/上调搁置进 `held_updates`（已有权重不回滚），无疑惑时零差异。held 状态机 `held→replaying→applied|applied_unverified|discarded`：replaying 与回执（`replay_submitted_at+batch_id`）写同一 SQLite 事务；崩溃恢复见回执置 `applied_unverified`（不重复提交，宁漏勿双计），无回执重试至 `replay_attempts=2` 后丢弃。
- **画像更新台账（Phase 0）**：新增只追加审计表 `profile_update_ledger` 与 `soul/ledger.py::ProfileLedger`。每个画像写点在**动作结束后一次 INSERT**（`outcome=success|failed`、before/after 摘要、`diff`≤2000 字符、`source_refs`、`turn_id`，并为后续 Wave 预留 `gate_verdict`/`held_id`）。台账为 best-effort 观察者:写失败只 WARNING、绝不阻断底层写入。枚举写点全挂钩(8 点 + 发现的 `init_soul_build` 额外点):对话偏好覆写/整份重建、dislike 清池、反馈批偏好/重建、管线各层 updater、推测 promote/confirm/reject、12h 整理 apply/revert、init 建像、cognition sync。清单进 `docs/modules/soul.md`(新写点纳入 code review 义务)。新增 CLI `openbiliclaw ledger [--line] [--days] [--write-point]`。
- **觉察证据链（Phase 0）**：`AwarenessNote` 新增生成式 `note_id`、`source_event_ids`(本轮 cursor 消费的事件 id)与 `source_event_ids_approximate`(归属为按轮非按 note)。`analyze()` 新增可选 `source_event_ids`,觉察 prompt 一字不动(回放不变性);`cognition_cycle` 传入每批事件 id。向后兼容旧数据。是 Wave B 疑惑 evidence_refs 的前置。
- **对话学习串行队列（Phase 1）**：`learn_from_dialogue` 改由 `DialogueLearnQueue` 单 worker 串行消费,消除相邻轮并发 read/merge/write。worker 自持生命周期(不入 `cancel_all` 注册表):热重载在 cancel_all 之前 pause-drain 旧队列、成功停旧启新、失败回滚 resume;进程退出经 shutdown 钩子 drain。
- **对话窗口 + 回灌 + 结算（Phase 1）**：对话历史截断到最近 `DIALOGUE_WINDOW_TURNS=20` 轮(≤窗口字节不变);durable popup + scope='chat' + completed 的 `chat_turns` 在重启后回灌恢复线索(CLI/probe/confusion 不回灌)。`build_dialogue_insight_prompt` 收敛为模块级静态 system + `sort_keys`(入 invariance 清单)并注入活跃清单(推测按 domain/洞察按内容 hash8/疑惑按 id);`extract()` 返回 `{candidates, settles}`,`learn_from_dialogue` 仅 scope='chat' 处理 settles(单一所有权,白名单=当轮注入清单),结算调既有函数并进台账(带 turn_id、幂等)。
- **八平台真实只读 E2E 暴露的五处边界问题已修复**：在隔离数据目录中使用真实登录态验证 B 站、小红书、抖音、YouTube、X、知乎、Reddit、Bangumi 的鉴权与只读取数后，修复了：(1) 并发 `/api/sources/status` 在共享 SQLite connection 上重复执行 X 健康表 DDL/SELECT 导致偶发 500，现改为首建单飞 + 每次操作独立短连接，429 读改写在同一 `BEGIN IMMEDIATE` 事务内；(2) 活体探针把 60 秒“可复用窗口”误当成用户可见验证期限，现显式验证 60 秒后仍会重新出网，但上次成功在 6 小时内继续诚实显示“已验证”；(3) 小红书多页 partial 每页都上报 5 时 `scope_counts` 错取最大值 5，现以合并后的 scope-aware canonical 条目数作下限；(4) 抖音 MAIN-world fetch tap 在两种 unpacked 目录布局间只尝试一个资源路径，现依次尝试 `dist/main/...` 与 `main/...`，background/content 两条重注入路径一致；(5) `fetch-douyin` / `fetch-xhs` / `fetch-youtube` 等到 `timeout` / `failed` 后不再以退出码 0 冒充 smoke 成功。修后真实复测：活体成功 61 秒后 B 站/抖音/Bangumi 仍为 `verified`；`/api/sources/status` 40 并发下 120/120 为 200 且契约有效，推荐接口 60/60 为 200，日志零 SQLite 错误/500/Traceback；小红书多页真实返回 saved 197 + liked 231，`scope_counts` 与 428 条 canonical signal 逐项相等；B 站历史 3/3、抖音 direct feed 2/2、X likes/bookmarks 1+1、Reddit 搜索 3/3、知乎 300+423+16、YouTube 3+5+0 均跑通。Bangumi `/v0/me` 首次成功，随后 collection 在用户当前 `network.mode=direct` 下两次按预期报海外直连 timeout（错误文案已明确建议 system/custom），未伪装成登录失效。Chrome 当时仍运行未重载的旧 bundle，所以抖音扩展 smoke 仍记录旧单路径错误并以新退出码 1 诚实失败；新双路径 bundle 已经 Chrome/Firefox build + 单测验证，但真实已登录浏览器的该分支必须在扩展重载后再验，不能冒充真机成功。全程未执行点赞、收藏、关注等平台写操作；两轮隔离凭据目录与测试服务均已清理。
- **用户反馈链路的五个实机问题已收口**：首页诊断现在把 B 站/X/画像的「账号同步」问题与八个平台的「来源接入」问题分栏组合展示，逐个平台复用后端 `detail`，缺凭据、过期、失败、受阻、限流与未知状态都能定位；正常的待验证/同步中不误报。`fetch-x` 的真实点赞/书签请求也写入与 discovery/账号同步相同、且绑定凭据指纹的 `XSourceHealthStore`，所以成功 smoke 会立即把 X 从“待验证”升级为有请求证据，401/403/429 同样留下可解释状态。
- **恢复几十个旧桌面标签页不再把后端打满**：隐藏页启动时不 hydrate、不建 runtime WebSocket，并在切到后台时关闭实时流、取消推荐/runtime/库存/activity/init 的重试定时器；重新可见后按 15 秒 freshness 单飞恢复。后端再以 1 秒推荐快照 single-flight 和逐项 saved-status 短缓存兜底旧版标签页的同时启动风暴，推荐变更会主动失效缓存，交互结果不被旧快照遮住。
- **配置与来源凭据改为只写秘密**：`GET /api/config` 和 `GET /api/sources/credentials` 即使带旧参数 `reveal_keys=true` 也只返回掩码，桌面 Web/扩展不再请求 reveal 版本，扩展 storage 不再缓存明文 API Key；来源表单不再宣告/渲染“复制原凭据”，页面明确只显示脱敏预览和保存状态。PUT 的空值/掩码回显仍保持“保留现值”语义。
- **空字符串布尔环境变量不再破坏配置接口**：`SchedulerConfig` 的 `enabled`、插件断开暂停、画像整理/归档和自动更新相关布尔字段统一在加载边界经 `_coerce_bool` 归一；例如 `OPENBILICLAW_SCHEDULER_ENABLED=""` 现在得到真实 `bool` 默认值，不再出现 CLI 看似关闭、`GET /api/config` 却因 Pydantic 类型错误 500 的分叉。
- **账号同步报错现在能指出具体环节与原因，不再只剩一句“同步出错”**：`account_sync_state.json` 新增有界、去重、结构化的 `last_sync_issues=[{stage,kind}]`，分别记录 B 站观看历史 / 收藏夹 / 关注列表、X 点赞 / 书签及画像分析阶段；B 站错误进一步区分登录失效、限流、网络、超时与接口异常，画像分析区分未配置模型、模型不存在、鉴权失败、额度用尽、连接失败、SSL 证书失败、限流、服务端错误、超时、无效响应与内容合规拒绝。`/api/runtime-status` 同步下发 `last_account_sync_issues`，后端基于整轮问题集合统一生成安全中文文案与 warning/error 严重度，混合故障会逐项说明、成功环节仍保留且只对可自动恢复的问题承诺重试；原始 provider 文本继续仅供诊断。MemoryManager 的显式白名单与 API/桌面归一化链路已补齐，旧状态文件保持兼容，畸形 issue 行不会进入展示。
- **X 被限流不再误报成整个账号同步故障，也不会绕过冷却继续请求**：真实用户反馈中，来源状态卡已明确显示 X 为 `rate_limited`，但同一时刻账号同步的 likes / bookmarks 路径既不读取共享 `XSourceHealthStore`，又把 `XRateLimitError` 压成通用 `error`，首页于是出现「账号同步出错，稍后会自动重试」，看起来像 B 站登录或整条同步链路坏了；更糟的是 discovery producer 虽已退避，账号同步仍会从旁路继续访问 X，同一轮 likes 收到 429 后还紧接着请求 bookmarks。现在 API runtime 与 OpenClaw 装配都只在 X 来源启用且 Cookie 存在时构造定时 X 同步；API runtime 的账号同步与 discovery producer 复用同一健康存储，进入 429 / 登录失效 / 403 状态时在出网前跳过 X 子路径，现场首个失败会立即写入健康状态并取消本轮第二个请求，成功则清理共享退避。`last_sync_error_kind` 新增 `x_rate_limited` / `x_auth_expired` / `x_blocked`，X 429 以 warning 明确说明「仅跳过 X，B 站等其他来源不受影响、冷却后自动重试、无需操作」，桌面 Web 也开始真正消费后端已有的 `last_account_sync_severity`，warning 不再套通用 error 样式。
- **「换一批」恢复为默认去重动作，不再伪装成批量“不喜欢”**：桌面 Web 删除容易误解的“换一批时忽略当前”开关；桌面、移动 Web 与扩展 side panel 统一把当前卡片 ID 作为 `excluded_bvids` 提交，后端继续叠加推荐历史与持久化已看身份三层硬去重。成功换批只写一条 satisfaction-neutral、证据强度 `0.1` 的 `reshuffle` 批次事件，metadata 保留有界的排除/返回 ID、批次大小和平台作用域，不再逐卡 fire-and-forget `dismiss`。新增 SQLite `seen_items(item_key)` canonical 已看账本：单条与批量 `view` 事件都在同一事务内 upsert，旧库按 `seen_items_backfill_state` 增量回填全部历史事件，彻底移除“只扫最近 2000 条”窗口；recommendation、discovery、raw/readiness、平台库存共用同一账本，B 站仍暴露 raw BVID 兼容旧调用。迁移测试覆盖第 2001 条以前的浏览记录，四端契约测试锁定当前卡排除、空批保留与单批事件语义。
- **手机版二维码不再显示过期的局域网 IP**：桌面 Web 的二维码抽屉此前以 `_cachedLanIp || requestJson(...)` 短路，首屏预取的地址一旦写入模块级缓存就永不失效——换 Wi-Fi、插拔网卡或切网段之后，抽屉里的地址会一直停在页面打开那一刻的值，重开抽屉也不会重查，只有整页硬刷新才更新，于是手机扫到的是一个已经打不开的旧 IP。后端 `/api/qr-info` 又和 `/api/health` 共用 30 秒 TTL 缓存，即使前端重查也可能再拿到一份陈旧值。现在抽屉每次打开都重新请求 `/api/qr-info`，缓存降级为「请求失败时的兜底」——失败时保留上次可用地址，不会掉回 `127.0.0.1` 并弹出误导的 `--host 0.0.0.0` 排查提示；`/api/qr-info` 改走新的 `_fresh_lan_ip()` 绕过 TTL 实时探测，探测本身要 spawn `ifconfig` / `ip` 子进程，因此放进 `asyncio.to_thread` 以免阻塞事件循环，结果同时回写共享缓存供 `/api/health` 复用。扫码是低频的用户主动操作，实测每次探测约 15ms（`/api/ping` 基线 2.3ms），面板打开无感。插件 popup 入口本来就每次重新请求、只受后端 TTL 影响，随后端修复一并解决；移动 Web 是扫码的目标页、CLI 无此入口，两者不涉及。真机验证：隔离 project root 起 serve-api，桌面 Web 连开三次抽屉产生三次 `/api/qr-info` 请求（旧代码为 0 次），地址为当前真实 en0 地址；再注入只让 qr-info 失败的 fetch，抽屉仍回落到缓存地址且不显示 loopback 提示。本次只修「取到的值会过期」，`_detect_lan_ip()` 的**选取**启发式不变（仍是 `ifconfig` / `ip` 文本序里第一个 RFC1918 地址，不区分物理网卡与 utun / bridge / 虚拟机网卡），多网卡下选错网卡是另一个待办。
- **Windows / macOS 桌面包升级后都能切到新版本进程**：Windows 此前安装前虽会结束旧 `OpenBiliClaw.exe` 进程树，但安装完成后的启动仍是可取消的 `postinstall` 勾选项，且 `/SILENT` / `/VERYSILENT` 会因 `skipifsilent` 完全不启动；现保留 Restart Manager + `taskkill /T /F` 与 `RestartApplications=no` 防重复恢复，安装成功后由 `[Run]` 无条件从 `{app}\OpenBiliClaw.exe` 启动刚写入的程序。macOS DMG 天生没有安装完成回调，不能假装“拖完就自动执行”，因此在 DMG 根目录新增显式 `安装并启动 Install OpenBiliClaw.command`：先把新 app 用 `ditto` 暂存到 Applications 同卷目录，校验 `CFBundleShortVersionString` 与 deep/strict code signature 后才退出旧菜单栏实例；优雅退出请求本身也受等待窗口约束，避免无响应 App 让 `osascript` 永久卡住，超时后才 TERM/KILL，并只清理由旧 OpenBiliClaw.app 拉起的内置 Ollama。随后旧 app 移入同卷备份、新 app 原子换位，安装后再验一次；拷贝失败、校验失败或信号中断都会恢复旧 app，成功才 `open -n /Applications/OpenBiliClaw.app` 并等待精确路径进程出现。助手不会自动删除 quarantine 或绕过 Gatekeeper；传统拖拽仍保留，但升级时必须手动退出、替换和重开。除静态打包契约外，新增 macOS-only 真实 E2E：编译并 ad-hoc 签名 1.0.0/2.0.0 最小 app、生成并只读挂载真实 DMG、从盘内执行助手，验证旧 PID 退出、版本与签名更新、新 PID 启动及同卷暂存/备份清理。

## v0.3.181：候选池份额公平 + 平台 Tab 成为真正的推荐作用域（2026-07-20）

- **候选池份额不再被超份额来源永久饿死**（真实生产数据：本机池 300/300 中 reddit 占 169（份额目标 25，超 7 倍），bangumi / douyin / youtube 等全为 0；另一台机器 B 站挤死 bangumi，表现为「初始化补一批后 bangumi 永不再补货」）。根因是份额只在生产端按 `min(自身缺口, 全局缺口)` 执行、入池端只看全局满不满：全局池一满，欠份额来源的缺口被清零、producer 永不被调度，already-evaluated 的行也抢不到坑。四处协同修复——**生产端** `_source_requested_count` 改为各源自身份额口径（缺口只受自身 raw ceiling 钳制，全局满不再清零）；**入池端** `DiscoveryCandidatePipeline` 两轮录取（欠份额行先入，超份额行仅作可用性兜底、绝不 reject），并把欠份额来源排到评估队列取行窗口前面；**再平衡** drain tick 在入池前从最超份额来源每 tick 退坑 ≤3 行（最低分最老、置 `pool_status='stale'`，仅在欠份额来源确有 `evaluated` 供给等待时触发），让池组成以温和速率单调收敛到配置份额；**Producer 内部闸**（真实 E2E 发现的死结：`bangumi producer skip: reason=pool_full`）——六个 producer 的内部 `_candidate_pool_full()` 原本调全局 `pool_full()`，全局满时欠份额来源永不生产 → 无 `evaluated` 供给 → rebalance 永不触发 → 池永远满，现统一经 `pool_full_for_source(family)` 让欠份额来源即使全局满也放行生产；**装配收敛**（再一处 E2E 死结：生产 serve-api 用 `CandidateEvalCoordinator.run_forever()` 替换了挂着再平衡与份额摘要的 `_loop_candidate_eval`，使 Phase 3/4 成为死代码）——再平衡与摘要收敛到 `run_pool_share_maintenance()` 单入口，coordinator 经新增 `pre_admit_hook` 每 tick 在入池前调用，与 legacy drain 两装配互斥、单轮至多一次退坑；**禁用来源存量行**（又一处 E2E 死结:配置只剩 bangumi+reddit 参与份额,但池里 bilibili 141 + xhs 7 行来自已禁用来源、不在份额表里,退坑只遍历在册来源 → 这 148 行永久占坑、bangumi 缺口无坑可腾)——退坑候选集现纳入不在份额表里的来源族（按 target=0 全额计超额、归族后再算），禁用来源的存量行可被回收；**评估端份额感知**（链路最后一环:占坑者钉满池 → coordinator 空转不评估 → 欠份额来源永远到不了 `evaluated` → 退坑触发条件「有 evaluated 等待」永不满足,第三层鸡生蛋;且 claim 不分份额,超份额积压先烧光评估算力,评估完又被第二轮兜底填回坑）——退坑的「等待供给」判定放宽到 `pending_eval`+`evaluating`+`evaluated` 全部非终态（池满也能退坑,退的本就是超份额最低分行、质量损失有界),评估 claim 复用 preferred 模式让欠份额来源的 pending 行优先出队（同时修 token 浪费）。每源 `available/target/deficit` 摘要在变化时打一条 INFO，退坑与第一轮跳过均有日志。未注入份额策略（旧测试 / OpenClaw one-shot）时 admission 与 producer 闸行为均与现状逐字节一致。（详见 [runtime](modules/runtime.md) / [discovery](modules/discovery.md) / [storage](modules/storage.md) 模块文档。）
- **PC Web 平台 Tab 从「结果过滤器」变成真正的推荐作用域**：此前切到「知乎」后点「换一批 / 加载更多」，前端仍请求全平台推荐、后端仍从全平台候选池选片，返回里没有知乎内容时当前 Tab 就继续空白——Tab 看着像平台入口，实际只是对已装入卡片的本地过滤。现在 `POST /api/recommendations/{reshuffle,append}` 新增**可选 additive** `source_platform`（别名在 Pydantic 边界 canonical 化，未知值返回 422，绝不静默回退到「全部」或 B 站；省略或空字符串保持旧行为，旧客户端不受影响），`serve / reshuffle / append`（含 `*_with_result`）新增默认空的 keyword-only 同名参数。平台作用域**只缩小候选集合**：跳过跨平台保底补位，curator 打分、amplification guard、embedding/MMR、topic/style/broad-topic 多样性、视觉加成、文案读取、推荐历史写入与 shown 消费全部复用既有实现，绝不是「先生成混合批次再过滤结果」；返回前校验跨平台行，发现即记 ERROR 并丢弃，不让泄漏进响应。平台定向批次不足 limit 时复用现有 `request_replenishment(..., force=True)` 唤醒后台补货，不在 HTTP 请求内同步跑 discovery。**四端范围**：后端接口是 additive，只有 PC Web 新增平台 Tab 行为；移动 Web、扩展 popup / side panel 与 CLI 没有该交互，继续走不带平台的兼容路径，行为不变。
- **平台 Tab 显示真实可推库存**：新增只读 `GET /api/recommendations/platform-availability` → `{total_available, by_platform}`，让用户能区分「当前页没装入」和「该平台暂时没货」。数字与平台定向选片共用同一套 servability 口径（fresh、非 dislike、达 admission floor、文案与分类齐全、链接可用、未被 delight 认领、未进推荐历史、未处于近期已看窗口，并遵循当前 topic window），由 storage 的单次隔离只读事务物化同一份行集合得出，因此 `total_available == sum(by_platform)` 是结构性成立而非两次独立查询的巧合。读取失败返回可诊断 5xx，前端保留上一次成功快照、首次未成功时显示未知态，绝不把失败伪装成全零（全零会读作「哪个平台都没货」并关掉所有平台的自动续页）。**顺带修掉一处既有分叉**：`get_pool_candidates_for_platform()` 原先自建 servable 查询并用裸列 `COALESCE(NULLIF(source_platform,''),'bilibili')` 比较，既漏掉 `source_platform` 为空、只能靠 `zhihu-hot` / `xhs-extension-task` 策略前缀归族的 legacy 行，也缺 `count_pool_candidates()` 的 per-topic 窗口——于是它能选出计数口径里根本不存在的行（`list_servable_pool_platforms()` 同时报告该平台无货）。现统一改从 canonical available 集合取整行、按 `_pool_source_family()` 归类，`strict_platform_candidates(p) ⊆ available_candidates_for(p)` 恒成立。
- **惊喜推荐必须等正式推荐词完成后才开始晋级**：惊喜评分与朋友式推荐词原本是两条异步链路，`precompute_delight_scores()` 会在 `pool_expression` 尚未生成时把 `relevance_reason` / topic 兜底写成 `delight_reason / delight_hook`；数据库又只检查这两个字段非空，于是高分候选可能先于正式推荐词进入 pending API、WebSocket 和三端页面，而普通推荐早已有独立 copy-ready 闸门。现在状态门被前移：打分 backlog 只领取 `pool_expression / pool_topic_label` 均已生成的内容，未生成推荐词的行连 `delight_score` 都不会写；存储层条件 UPDATE 还会拒绝未就绪文案或非正式快照，正式文案发生竞态变化时整次晋级失败、等待下轮。读取出口继续要求 `delight_reason / delight_hook` 与正式文案逐项一致，动态阈值也只统计 copy-ready 样本。旧版已错写的行会先被隐藏，正式文案生成后由 backfill 自动改写；普通推荐的互斥认领仍要求“高分 + 正式文案快照已同步”。profile floor 或动态阈值升高时，旧门槛下留下的展示快照会被自动清空并释放正常推荐。
- **真实推荐词请求不再把合法的多行 singleton JSON 判坏**：使用当前 `openai_compatible / deepseek-v4-flash` 对真实 B 站候选重跑推荐词时，服务商连续 HTTP 200 并返回合法 `{"bvid","expression","topic_label"}`，生产解析却落成 `ExpressionBatchMalformed`。根因是 `extract_llm_json_list(..., allow_singleton=True)` 把 root dict 原样交给“必须是 list”的 coercer；旧测试用单行 JSON，恰好被后面的 JSONL fallback 偶然救回，掩盖了契约失效，而真实 provider 的 pretty-printed 多行对象无法走 JSONL。现在 singleton 分支显式包装为单元素列表，单行与多行对象统一命中正式解析路径；测试改为多行 fixture，并新增推荐引擎单候选落库回归。
- **初始化不再预估耗时，改为只报「已用时 + 已完成多少」**（真实用户反馈：阶段 2 写着「本阶段通常约 3 分钟」、阶段 4 写着「约 5 分钟」，实际跑了半小时到 45 分钟以上，用户结论是「你不要给它预估啊」）。根因不只是常数标定过时（`_STAGE_ETAS` 沿用本地快模型时代的 90/180/70/300 秒），更在于**预估这件事本身就不可靠**：一次初始化的真实耗时同时取决于勾了几个平台、拉到多少历史、以及 AI 服务的快慢，任何一个固定数字都会对一半用户说谎，而每次说谎都会让健康的长时间运行被读成「卡死了」。现在**彻底删除** `stages[].eta_seconds` 及其全部管线（后端不再发布，插件 popup / 桌面 Web / setup 向导三端也不再渲染任何预测），运行中阶段行改为只显示后端本来就在发布的客观事实——「已用时 12 分钟 · 已完成 3/6」，没有分批进度的阶段（如单次画像生成）则只显示已用时。进度条同步去伪：原先靠 `1 - e^(-已用时/预估)` 伪造流动的曲线、以及无进度时那个凭空占一半的 0.5 兜底，全部移除——只有真实 `done/total` 能推动进度条，没有真实计数的阶段直接走代码里早已存在的 indeterminate 流动条，单调钳制（`maxPct`）保持不变，进度条不会再暗示一个阶段已经接近完成。耐心安抚只说一次（等待中显示「只要还在出结果就不会被打断，慢一些是正常的」，这在 v0.3.180 之后已经字面为真），不再逐行重复；启动前的说明也不再给「4–20 分钟」这类区间，改为诚实说明耗时差别很大、期间可离开页面且进度保留。`openbiliclaw init` 的 CLI 提示同步去掉分阶段时间承诺；CLI 自己的 `_run_with_progress` 控制台心跳保留其 ETA（它是另一个界面，本来就同时打印已用时并会明确切到「已超预估、仍在处理」）。
- **惊喜卡自动轮播由 4 秒放慢到 60 秒**（用户反馈：「自动切换频率太高了」）：4s 是初版占位值，可一张惊喜卡要读标题 + 推荐理由 + 正文摘录，十几秒都未必读完，结果卡还没看清就被换走，想看完只能手动倒回去。桌面 Web 与移动 Web 统一提到 60s，并把这个间隔从两处散落的魔法数字提成带校准注释的 `DELIGHT_AUTO_ADVANCE_MS` 常量（两端同名，注释互相指认，避免下次只改一边）。互动保护与手动操作不变：正在惊喜卡打字 / 聚焦输入框 / 有草稿时照旧跳过这一轮切换，拖拽与上一条 / 下一条仍可随时快进。
- **新增来源 skill 吸收 Bangumi 多轮修复经验**：权威接入指南不再只覆盖「把抓取、discover、配置和推荐卡接上」，补齐这次真实适配暴露出的通用护栏：匿名可用但支持可选凭据的第三类鉴权、observed identity 与 verified evidence 分离、后端独占账号解析/准入、字段省略/清空/掩码的局部更新语义、2xx warning、关闭来源时仍展示凭据状态、外部 schema 类型防御与真实反例、多 lane 公平分页和去重后计预算、cursor/429 关键词生命周期、跨平台作者/内容 ID 文案、按真实传输层给网络提示，以及 discovery smoke 与收藏→事件→初始化 smoke 分开验收。Codex/Claude 两份 `add-platform-source` skill 已同步；Bangumi 模块文档中关于 popup 准入 guard 仍为 `xfail` 的过时描述也一并纠正为当前已完成状态。

---

## v0.3.180：慢 AI 服务不再被误杀——初始化超时改为「有进展就不打断」（2026-07-20）

- **初始化不再因为「AI 服务慢」而被判死刑：固定墙钟改为「有进展就不打断」的双重期限**（真实用户反馈：商汤 deepseek-v4-flash 健康但慢，进度条明明还在推进到 2/6 批、AI 已处理 280s，却被「偏好分析等待 AI 服务超过本轮 15 分钟上限」直接掐断）。根因是阶段 2 用 `asyncio.wait_for` 套了一个开跑前就算死的墙钟，而墙钟根本分不清「卡死」和「慢但在出结果」，任何比标定慢的服务商都会在出结果的途中被杀。现在改为**空闲期限 + 绝对上限**双限制：只有真正的分片完成回调（心跳 tick 不算）才刷新「上次进展」时间戳，10 分钟没有任何新结果才判定卡死，45 分钟绝对上限兜底防无限续期；两种失败给**完全不同的可操作文案**——空闲超时说「长时间没有返回任何新结果」并指向 Base URL / 模型名 / 代理排查，绝对超时说「总时长超过上限」并附上已完成批次、建议换更快的模型。阶段 4（首轮内容池，有真实进度信号）同样启用双限制（空闲 15 分钟 / 绝对 45 分钟）；阶段 3（生成完整画像）是**单次 LLM 调用、没有分片进度可读**，空闲判定对它天然失真，因此只放宽绝对兜底、不加空闲限制。同时按「很多 API 就是很慢」的实情整体放宽其余初始化上限，且每个新值都在注释里写明它是**卡死兜底、不是性能预期**：画像生成 6→30 分钟、首轮内容池 10→45 分钟、阶段 1 全局采集 10→30 分钟、B 站/Bangumi 单源 4→10 分钟、X 单源 3→8 分钟。插件、桌面 Web、setup 向导三端的初始化耗时提示同步补上「历史较多或模型较慢时可能超过 1 小时，只要仍有进展就不会被中断」，GUI 引用的 `stages[].progress.max_seconds` 也改为发布新的绝对上限，避免界面继续报一个已经不再生效的数字。取消语义不变：`CancelledError` 仍如实向上传播，被中断的初始化仍记为 `cancelled`。
- **初始化「停滞」提示不再冤枉慢模型**（用户实测：商汤 deepseek-v4-flash 单批分析约 140s，进度正常推进却全程报警）：工作单元停滞判定此前直接复用心跳阈值（30s×3=90s）——但心跳周期只说明"连接还在"，与"一个工作单元该跑多久"毫无关系，慢模型/远程网关跑一批分析动辄数分钟，于是健康运行也被渲染成告警，读起来像出了故障。现改为自适应：下限 300s，并按**本轮已展示的最慢工作单元 ×1.5** 动态放宽，再以后端该阶段的硬上限（`progress.max_seconds`）封顶，保证真超时前仍会预警；文案也从「已 N 分钟没有完成新的工作单元」改为「这一步已等待 N 分钟，比本轮此前的节奏慢」。心跳断连判定仍用 90s（校准依据未变）。三端（插件 / setup 向导 / 桌面 Web）同步，静态契约测试锁住新常量与文案。
- **用户社区入口改为 Discord，下掉已失效的微信群码**：README CN/EN 里的微信二维码标注「7 天内有效」，但实际停留在 6 月 8 日生成的那张（6 月 15 日即过期），用户扫到的是死码；现把该位置换成永久有效的 Discord 邀请二维码（`docs/images/discord-community-qrcode.jpg`，与旁边 QQ 格视觉对齐；图片本身包在邀请链接里，手机扫码与桌面点击都可用），并删除不再被引用的 `docs/images/wechat-user-community-qrcode.jpg`。该邀请设为永不过期，避免重蹈微信码「标称 7 天有效、实际挂死 6 周」的覆辙。QQ 群入口不变。

---

## v0.3.179：降级不再连坐无关功能，用户文案说人话（2026-07-20）

- **降级模式不再连坐 LLM 无关的修复功能**（Windows 真机排查第三波）：降级白名单当初只考虑「修 LLM 配置」一条恢复路径，把平台源状态（`GET /api/sources/status`，契约明确只读本地状态绝不出网）、测试连接（`POST /api/sources/<平台>/verify`，验的是平台 cookie 与 LLM 无关）、embedding 修复（`/api/embedding/repair`，只碰 config + 托管 Ollama）全部 503 连坐——设置页平台源 tab 报误导性的「请确认后端服务可用」、测试连接透出原始 503、「一键启用本地 Ollama」报假的「检查后端连接」。三个端点都只依赖 config/database（降级上下文齐备），现加入白名单：降级期间用户可以边修 LLM 配置边把平台登录态配好验好。同时插件「语义去重未启用」横幅补上时机门控：仅在**后端健康且已初始化**时显示——降级时唯一正事是修配置，未初始化时 init 清单里的「向量模型可用（推荐，非必须）」行才是 embedding 信息的归宿，而「可能刷到重复视频」在没有推荐流之前毫无意义。
- **用户可见文案不再说「降级」**（用户反馈：「降级」是工程内部概念，对用户而言 LLM 坏了产品就是坏了，不存在"降级运行"）：插件底部提示与初始化引导、设置浮层横幅、桌面 Web 恢复摘要、移动端状态角标、`POST /api/init` 拒绝与 init-status detail、降级下保存配置的返回消息、CLI 状态面板标题，统一改为直白的「AI 服务配置有误，修复并重启后端」措辞；代码、日志与 API 字段里的 degraded 术语保留（面向开发者）。

---

## v0.3.178：降级新装机直达初始化引导（2026-07-20）

- **插件在「降级且从未初始化」时优先走初始化引导，而非只给修复入口**（用户实测反馈）：v0.3.177 的降级态把未初始化的新装机也导向「去设置修复」，但完整初始化旅程的第一步本来就是配置 LLM——现在推荐页借 `/api/runtime-status`（降级白名单内）区分「从未初始化」与「初始化过后来降级」：前者照常渲染「开始初始化」面板，前置检查清单如实显示降级阻塞原因（来自 init-status 的降级 detail），并保留「去设置修复 →」快捷按钮；后者维持纯修复态。无 runtime 快照时保守落回修复态。

---

## v0.3.177：初始化与安装全流程加固——降级不再是死胡同（2026-07-19）

- **修复降级模式下 setup 向导整页瘫痪（Base URL 填不了、配置修不了）**：来源登录态契约把平台源清单抽进 `/shared/source-status.js` 供 setup / 桌面 Web / 插件三端共用，但 degraded-mode guard 的静态恢复面白名单没跟上——降级时该脚本被 503 拦截，向导顶层 `SourceStatus.SOURCE_KEYS` 直接抛 TypeError，整页事件全灭：切换服务商无反应、OpenAI 兼容接口的 Base URL 字段永不出现，用户被锁死在无法修复配置的死循环里（真实 Windows 用户复现：deepseek 缺 api_key → 后端降级 → 向导瘫痪 → 无法改配其他 provider）。现将 `/shared/` 加入恢复面放行，并在降级测试中锁住该脚本必须可加载。
- **setup 向导不再提供「本地 Ollama」当聊天 provider**：随装的 Ollama 定位是 embedding（bge-m3），聊天模型需要用户自行 `ollama pull` 且小模型跑内容管线质量不达标，首次配置把它摆在选项里等于引导新用户走进坏体验。向导下拉移除该项（API Key 相应变为全 provider 必填）；桌面设置页、config.toml、后端 provider 注册表不受影响，进阶用户仍可在设置页选 ollama。E2E 与向导测试同步迁移到 deepseek + 显式填 key 的路径，并断言下拉中不再出现 ollama。
- **本地 Ollama「不当聊天默认」的口径在所有 onboarding 面统一**：继 setup 向导下拉移除该项之后，把剩余入口对齐同一口径——`openbiliclaw init` 交互菜单、`scripts/agent_bootstrap.py` 人类安装菜单都不再把「本地 Ollama」列为聊天 provider（菜单序号相应收缩，`ollama` 仍作为来自既有配置 / 显式 flag 的合法值被解析，只是不再交互式提供）；`install.sh` 的 provider 清单与「Ollama 无需 Key」提示同步剔除，并把它标注为 embedding-only；`config.example.toml` 的 `default_provider` / `fallback_provider` 注释重写为「embedding 定位、聊天属高级用法」。此外向导对**已保存但已失效的默认 provider**不再静默丢弃：当 `/api/config` 回一个下拉里没有的 `default_provider`（如 `ollama`）时，`#msg0` 给出一条 info 提示「本地 Ollama 仅用于向量检索……请重新选择一个聊天服务商」，新增 Playwright 用例锁住该提示。`install.sh` 的安装后「下一步」还补上了图形化引导页 `http://127.0.0.1:<port>/setup/` 的指路（与 `docs/docker-deployment.md` 的宣传一致）。后端 provider 注册表、桌面设置页、`[llm.ollama]` 聊天支持一律不受影响。
- **初始化界面四项一致性修复**：(1) 初始化来源清单收口——桌面 Web 与插件侧栏此前各自硬编码一份平台列表，现统一从 `/shared/source-status.js` 的 `SOURCE_KEYS` 派生（标签优先取共享模块、本地映射兜底），并加漂移锁测试锁住三端与共享清单一致；(2) setup 向导 B 站轮询生命周期修复——离开步骤 1 时清掉 3 秒轮询并置空以便重进重启，且连续几次未检测到登录后把转圈行替换为中性可跳过提示（仍在步骤 1 继续轮询）；(3) 桌面 Web 在推荐失败点识别降级 503 信封（`details.status==="degraded"`）→ 走既有模型设置恢复流程而非通用重试 UI；(4) 跨端初始化文案对齐——向导「已初始化」采用桌面口径带设置页指引，插件未初始化提示改为按钮驱动文案。移动端按四端契约有意排除。
- **初始化状态的四类失败路径不再吞掉真实原因（铁律 7）**：(1) 后端重启打断初始化后，`reconcile_init_runs_on_boot` 只把行状态翻成 `failed`，却不降级 `stages_json` 里的 running/pending 阶段、也不写 `error_detail`，于是 `/api/init-status` 报 `interrupted` 却 detail 为空、还留着一个幽灵「running」阶段；现按 `InitCoordinator.reconcile_orphaned_run` 的语义把这些阶段降级为 `failed`/`interrupted` 并写入同款中文 `error_detail`。(2) 降级模式（LLM registry 构建失败）下 `POST /api/init` 原本回裸 `{"error":"llm_not_ready"}`、`/api/init-status` 也无降级分支；现两者都识别 `llm_registry_unavailable` 降级态，回可操作文案指向设置页修 LLM 配置后重启。(3) `llm_not_ready` 从不具体——chat 探针本知道失败原因（无效 API Key / 服务不可达 / 模型不存在），现经 `describe_llm_failure` 把分类原因透传进 `POST /api/init` 的 409 detail 与 init-status reason detail，探针严格度不变。(4) `_maybe_autostart_embedding_pull` 原只处理 MODEL_MISSING/MODEL_BROKEN，托管 Ollama 未启动（DIAG_NOT_RUNNING）时静默无操作；现复用手动修复端点同款守卫先启动托管 Ollama 再重新诊断，仍保持 best-effort、非阻塞。
- **插件推荐页在后端降级时给出修复入口，不再只说「接口没回」**：降级后端对业务路由统一回 503 信封（`status:"degraded"` + 具体 issue），`requestJson` 早已把它挂在 `error.details` 上，但推荐页把一切错误揉成通用文案「后端连上了，但推荐接口这会儿没回」，可操作的事实（LLM 配置坏了、设置页能修）被完全隐藏——降级时初始化引导也不会出现，用户无路可走（真实 Windows 复现）。现在识别降级信封为独立状态：标题「AI 服务配置需要修复」+ 后端返回的具体 issue 文案 + 「去设置修复 →」按钮直达后端设置浮层（复用既有降级横幅与「保存并提示重启」模式）。桌面 Web 已有降级恢复流程（v0.3.175），移动端维持既有排除（修配置属桌面/插件场景），CLI 本就透传真实错误。

---

## v0.3.176：来源登录态契约统一——让绿灯诚实（2026-07-19）

- **八个平台的「凭据已就绪」不再一视同仁**：此前设置页把强度天差地别的判断渲染成同一个绿灯——B 站只是数出 cookie 串里有三个字段名（全程不联网）、小红书与知乎靠浏览器 72h 心跳、Reddit 只看本地文件未超 7 天（注释明说绝不联网）、X 是上次请求没报 401、YouTube 干脆是硬编码常量；抖音则即使 cookie 完全有效也永远显示「状态待验证」。用户无从分辨「真能用」与「只是填了值」。现在每个来源如实标注结论来自哪种证据（`live_probe` / `passive_health` / `browser_heartbeat` / `local_file` / `none`），并配「测试连接」按钮当场发起验证。Bangumi 作为第 8 平台一并纳入契约：它打破了「不需要登录 ⟹ 无从验证」的隐含假设——公开收藏匿名可读，但配了个人令牌就能经 `/v0/me` 真验，因此 `verify_method=live_probe` 名副其实。
- **抖音「已验证」不再一分钟就过期**：`PROBE_OK_TTL_SECONDS=60` 一个常量身兼两职——既是写入门的探针复用窗口（防 msToken 轮换招风控，60 秒正确），又被拿去驱动 UI 的「已验证」新鲜度（60 秒太短，点完测试连接一分钟后又变回待验证）。拆出独立的 6 小时新鲜窗口并按 CLAUDE.md 铁律 3 记录标定来源，加测试锁住两者不许再合并。
- **移动端有意排除，并写进规格**：手机是躺着刷推荐的场景，填 cookie、点测试连接属于持有登录态浏览器会话的桌面与插件。移动端仍经 saved-sync 透出各平台的登录需求，不会对「这个来源需要登录」失明。按 CLAUDE.md 铁律 5 的四端契约要求，该排除在指标脚本注释与 spec 里显式声明，而不是默默少做一端。

---

## v0.3.175：海外出网默认跟随系统代理、Bangumi 身份诚实化与小红书封面修复（2026-07-19）

- **修复「小红书内容都没头图」：封面 URL 与字节都在扩展抓取时采集**：用户日志复盘定位到两个叠加缺陷。(1) **后台标签页懒加载图片永不升级**——搜索/创作者任务在后台标签页刮取，卡片 `<img>` 永远停在内联 `data:` 占位符，DOM 提取拿不到真实封面 URL（铁证：受影响后端 7-12~16 对 hdslb / ytimg / douyinpic 抓取全部正常，却从未尝试过一次 xhscdn 抓取——它手里从没有过可抓的 URL）；(2) 即使有 URL，服务端预取也是与轮换 token 过期、与本机 CDN 出网的赛跑。修复三件套：扩展 `cover-harvest.ts` 从 `__INITIAL_STATE__` 形状无关深扫按 note_id 回填真实封面 URL（循环安全、深度/节点有界；DOM 两路提取一律拒收 `data:`/`blob:` 占位符）；随后在页面上下文抓封面字节（token 最新鲜、走用户浏览器会话），转 base64 挂 `cover_data`/`cover_content_type` 随既有通道上报（每批 ≤12 张、单张 ≤1MB、4s 超时、best-effort）；后端把非 http(s) 的 cover_url 归一为空，`save_extension_cover` 校验（白名单 / `image/*` / 大小 / base64）后写入既有 `data/image-cache/`——缓存 key 本就剥离轮换 token，serve 零改动全走缓存命中，已知候选去重的笔记也先存封面以就地治愈存量。已在隔离 serve-api 用真实小红书页面采集的真封面端到端验证（入库→落盘→原 URL 与换 token URL 均缓存命中 200）。同期补齐链路可观测性（`image_cache` 模块此前零日志，本次定位只能靠「慢取日志里 xhscdn 完全缺席、其它 CN CDN 都在」反推）：`fetch_cover_bytes` 失败按 host 限频 WARNING 且 detail 携带真实上游状态码，image-proxy 失败补 DEBUG 关联行，xhs ingest 每批 INFO 汇报缓存封面数。
- **账号同步的 LLM 故障不再谎称「稍后会自动重试」**：画像分析因 LLM 不可用失败时，`last_sync_error_kind` 此前不写入，桌面 Web 只能落到通用文案「账号同步出错，稍后会自动重试」——但当真实原因是模型未配置（API Key/Base URL 被清空）或模型名不存在时，重试永远不会成功，用户被误导干等（真实用户日志复盘：provider 配置被手改清空 → 后端 degraded → 横幅仍承诺自动重试）。现在 `_persist_profile_analysis_error` 把 `classify_llm_unavailability` 的分类（`no_provider` / `model_not_found` / `rate_limited`）随错误一起持久化，`_user_facing_sync_message` 为前两类渲染指向设置页的可操作文案（「修复后会自动恢复」），`rate_limited` 如实保留自动重试承诺且 severity 降为 warning。文案仍由后端统一计算；**本次仅桌面 Web 消费该横幅**，扩展 popup / 移动 Web / CLI 不展示此状态。
- **未初始化时根路径直接引导进 setup 向导**：`GET /` 原本无条件 302 到 `/web`，只有打包版启动器(packaging/entry.py)会在首启时判断初始化状态并打开 `/setup/`——git / Docker 安装没有启动器，手动访问端口的用户在降级或未初始化时落到桌面 SPA 而非配置向导。现在服务端复刻 `_decide_landing_path` 的规则：后端处于 degraded 模式、或画像未初始化（`is_profile_ready()` 明确为 False）时 `/` 302 到 `/setup/`；就绪或探测结果未知时保持 `/web`（SPA 的引导初始化卡片仍是安全网）。仅当 setup 静态目录存在时才改道。
- **修复 LLM 配置失效时访问 Web 只看到原始 503 JSON**：degraded-mode guard 原本只放行移动端 `/m`，却在静态路由执行前拦住 `/`、桌面 `/web` 及首次配置 `/setup`，所以浏览器直接显示 `llm_registry_unavailable` 响应，用户反而进不了可修复配置的页面。现精确放行四个静态恢复 surface 及其资源，业务 API 仍保持 503；provider-free 的 `GET /api/ping` 仅在降级时附带 reason / issues，桌面 Web 先用它识别恢复态，跳过推荐、画像、平台源等必然失败的 hydration 请求，再读取配置并自动打开「模型」设置，以中文说明补齐 Provider 的 API Key / 模型 / Base URL，保存后提示重启后端。正常模式的 ping 响应保持兼容。
- **修复惊喜推荐文案渲染成结构化数据**：模型偶尔把整批结果塞进单个字段（如 `{"expression": [{...}, {...}]}`），而各处消费点都用 `str()` 无条件转换，于是 Python repr 通过了非空校验并作为推荐文案落库，用户在惊喜卡正文看到 `[{'expression': ..., 'topic_label': ...}]`。全程无任何日志，因为解析"成功"了——只是值不对。现对五个会持久化 LLM 文本的源头统一加类型守卫：推荐文案单条/批量、推荐分类（`reason` / `topic_group`）、发现分类单条/批量（`reason` / `topic_group` / `franchise_key`，经 `relevance_reason` 流向惊喜文案）。发现侧暴露面更大——`_clamp_score()` 遇到坏 score 返回 `0.0` 而非抛错，没有任何其他机制能拦住 repr 化的 reason。`update_pool_copy()` 另加一道基于解析的兜底（而非前缀匹配，后者既会漏 JSON/空白变体又会误伤含代码的正常文案）。**行为变更**：非字符串 `reason` 现在判为分类失败，不再持久化 repr。存量清洗脚本见 `scripts/clean_serialized_pool_copy.py`（默认 dry-run；已推荐的行写确定性 fallback 文案而非置空，因为待补文案查询会跳过已有 recommendation 的 bvid，置空只会让卡片变成空白）。
- **修复 B 站 Cookie 过期只显示英文报错**：桌面端本就有中文「请重新登录」分支，但 `last_sync_error_kind` 从引入起就被 `memory/manager.py` 的两处 key 白名单和默认状态静默丢弃，读盘后恒为空，UI 永远落到兜底分支直出 458 字符的英文原文。同一原因还被重复拼接三遍——history 阶段打 `/x/web-interface/history/cursor`，favorites 与 following 各自调一次 `get_nav_info()` 取 mid，同一个 -101 抛了三次，现已去重。Cookie 过期是预期的生命周期事件而非故障，因此改由后端渲染用户文案（原始英文保留在诊断字段），桌面按 warning 而非红色 danger 呈现，时间戳复用本地化格式化器（`formatUpdateCheckTime` → `formatLocalTime`）。**本次仅覆盖桌面 Web 与 API**，扩展 popup、移动 Web、CLI、OpenClaw 适配器暂不展示该状态。
- **修复账号同步并发覆写**：`sync_now()` 无互斥，而后台 `run_forever()` 循环与 OpenClaw 手动触发可同时进入，两者加载同一份状态、后写者覆盖对方的游标、错误串与 error kind。单纯的 `asyncio.Lock` 不够——API daemon 与独立 OpenClaw adapter 各自基于同一数据目录构造服务实例，实例锁互斥不到；且普通锁只是让第二个调用排队后再跑一遍冗余同步。现改为进程内共享 in-flight task + 状态文件旁的非阻塞 OS 文件锁，抢锁失败方返回 `already_running`，持锁进程崩溃由内核释放；`sync_if_due()` 在加入 in-flight 后重查游标，关闭 TOCTOU。
- **Bangumi 作者字段落地（原「作者字段恒空」限制解除）**：`bangumi_subject_to_content` 改为从 subject 行内 `infobox` 解析 `author_name`。两个 discovery 端点（`POST /v0/search/subjects`、`GET /v0/subjects`）本就内联返回完整 Subject，2026-07-18 实测 250/250 行都带 `infobox`，且 `GET /v0/subjects/{id}` 没有多出任何字段——因此制作方署名是**零额外请求**拿到的。Bangumi 没有统一作者字段（书籍叫「作者」、动画叫「导演」、游戏叫「开发」），故按 SubjectType 走优先级阶梯（书籍 作者→原作→作画→出版社，动画 导演→原作→动画制作→製作，音乐 艺术家→作曲→厂牌，游戏 开发→发行→游戏开发商，三次元 导演→编剧→主演），阶梯顺序由同一次 250 行实测的各 key 填充率标定并连同复标定要求写进代码注释。兼容官方三种 `value` 形态（裸字符串 / `[{v}]` / `[{k,v}]`，后两者只读 `v`，`k` 是「总导演/副导演」这类子标签而非人名），同 key 重复取首个非空值，多名字最多留 3 个、渲染长度硬限 80 字符且切在名字分隔符上（不会断在半个名字或未闭合括号里）；`infobox` 缺失、类型漂移、条目非映射、阶梯 key 全缺一律解析为 `""`，杜绝历史上 `COALESCE` 无法自愈的字面量 `"None"` 脏行。**遗留限制**：用户收藏 `GET /v0/users/{username}/collections` 内嵌的是 SlimSubject，不带 `infobox`，经该路径进来的条目 `author_name` 仍恒为 `""`（已记入 mapper docstring 与 `docs/modules/bangumi.md`「已知限制」）。
- **修复 Bangumi 扩展身份被前端护栏堵死（GUI 三面里没有一面能用）**：packaged setup、桌面 Web 与扩展 popup 各带一份逐字相同的前端前置判断——"仅选 Bangumi 且没填用户名/令牌就直接拒绝、不发请求"。这份拷贝早于三级账号阶梯的第三级（浏览器扩展在已登录 bgm.tv 页面上报的身份），看不见它：真机同后端进程、同 payload 实测，GUI 操作 `/api/init` 实际请求数为 **0** 并报"请填写个人令牌或公开用户名"，而同一 payload 直打后端返回 **202** + `warnings:["Bangumi 使用浏览器扩展识别到的账号 sai。"]`——正是铁律 5 的四面契约漂移。修法是把准入判定交回后端：setup 与桌面 Web 两处判断删除（后端在三级全空时才 `409 no_profile_signal_sources`，实测响应可读且已被两面如实渲染），三端 reason 文案同步补上"或先在浏览器登录 bgm.tv 让扩展自动识别账号"（原文案只提令牌和用户名，删掉判断后仍会误导）。Playwright 真页面回归（setup + 桌面参数化）钉住"仅选 Bangumi、不填任何凭据时 `/api/init` 必须真的发出"，回插旧判断后 4 条全部超时失败。**遗留**：`extension/popup/popup.js` 的同款判断（含内联拒绝文案）本轮未动，`tests/test_bangumi_web_surfaces.py` 有一条 `strict=True` xfail 钉住，修好后连 xfail 一并移除。
- **Bangumi 身份校验 fail-open 改为诚实 fail-open**：`_verify_bangumi_identity` 此前在任何异常下原样持久化未经校验的 DOM 上报用户名，且只打 **DEBUG**；而这条护栏当初正是为了防"时间线陌生人被抓成自己"。该结论标定于本节「海外出网默认值 `direct` → `system`」那条之前：当时默认 `[network] mode = direct` 连不上 bgm.tv（海外 CF），因此它对"零配置、不用令牌"这批目标用户**永远是空的**（默认改为 `system` 后，有可用系统代理的机器会真的跑到校验，没有代理的仍然走 fail-open）——隔离后端实测：`POST /api/sources/bangumi/identity {"uid":123456,"username":"sai"}` 这组胡诌配对返回 `{"ok":true,...}` 并落库，全程零 WARNING。fail-open 本身保留（fail-closed 会让国内零配置用户直接不可用），改的是诚实性：(1) 校验失败 DEBUG→**WARNING**，带真实原因（`bangumi identity: could not verify uid=… username=… against bgm.tv (timeout: …); storing the extension report UNVERIFIED`，铁律 7）；(2) 记录改写为 `{uid, username, verified}`，`verified` 仅在 bgm.tv 给出确定答复（2xx / 404）时为 true，接口响应同步回传；(3) guided init 与 CLI init 用到未校验身份时文案说实话——"（未经 bgm.tv 校验，可能不准）"并给出改用用户名/令牌的出路，已校验的保持原文案。**向后兼容**：旧记录无该键，一律按未校验读取（无法证明校验跑过），下次访问 bgm.tv 页面重报即就地升级；CLI `_load_extension_bangumi_username` 随之改名为 `_load_extension_bangumi_identity` 并返回 `(username, verified)`。
- **`verified` 标记改为对同一身份单调（sticky-true）**：对抗式 review 抓出上一条自己会擦掉证据——`_persist_bangumi_identity` 无条件用本轮结果覆盖，于是「第一次校验成功存 `true` → 第二次上报赶上 bgm.tv 超时」会把已拿到的校验证据改写成 `false`，guided init/CLI 随即对用户的真实账号谎报「未经 bgm.tv 校验，可能不准」，网络抖动还会让标记来回翻、每翻一次写一次盘。成功的交叉校验是关于某个 uid↔username **配对**的证据，不因我们后来连不上而失效，所以标记只允许往上抬：本轮成功→`true`；本轮失败但已有记录 uid 与 username 与本轮**完全相同**且已是 `true`→保持 `true`；uid 或 username 任一不同→是另一个从未验证过的主张，按本轮结果写。合并跑在 `update_discovery_runtime_state` 回调**内部**（`update_json_state` 先取进程锁+文件锁再从磁盘重读，回调看到权威最新值），并发上报不会互相覆盖；回调外那次读取只用于「确无新信息就跳过加锁与写盘」的幂等快路径。`POST /api/sources/bangumi/identity` 改为回传**落库后**的 `verified`，响应不再与读回的记录自相矛盾。
- **infobox 解析兑现「绝不落库字面量 None」的承诺**：同一轮 review 指出 `5a117d96` 的说明确有夸大——`_infobox_value_text` 当时把裸字符串**完全透传**（源数据里的 `{"key":"作者","value":"None"}` 就真的写成 `author_name="None"`，正是我们声称杜绝的那类脏值），且对列表元素的 `v` 无条件 `str()`（schema 漂移的 `{"v":["押井守"]}` 被制造成字面量 `"['押井守']"`）。现在 `v` 非字符串一律跳过、不做 `str()` 兜底；新增 `_is_placeholder_credit` 把「只是拼写出缺席」的值归一为 `""`：空串、语言级 null 字面量（`none`/`null`/`nil`/`nan`/`undefined`）、无歧义的 `n/a` 形式、以及纯标点（`-`/`——`/`/`/`?`/`（）`），裸字符串与两种 list 形态都过这道。边界刻意收窄，**不**过滤裸 `na`（可能是罗马音姓氏）、`无`/`未知`/`暂无`/`不明`（普通汉字，可能出现在真名里，属编辑散文而非序列化产物）、单字母与数字（可能是艺名）——误杀会静默删真实数据，理由连同名单写进代码注释并有测试钉住。顺带修掉截断在未闭合括号处收尾的问题（`…（総監督` → 回退到括号前），并把「整条 credit 就是一个长括号块时保留硬切、不抹掉真实署名」这一取舍显式写成测试。
- **收紧 `verified` 的定义：答复 ≠ 确认**：`verified: true` 现在**仅**表示 bgm.tv 正面确认了这个 uid↔username 配对（`get_user` 成功、`id` 与上报 uid 一致、用户名非空）。此前 404 与 uid 不匹配也记 `true`——上报 `{uid: 999999, username: "does-not-exist"}` 会落库 `{"username":"","verified":true}`，可我们从未确认过这个 uid 属于谁，bgm.tv 只是否定了用户名；叠加 sticky-true 后，同 uid 的后续上报遇到网络失败会把这个从未确认过的身份永久钉成 `true`。现在 404（无论有无上报用户名）、uid 不匹配、以及匹配成功但用户名不可用，一律 `false`；404 清空用户名的行为不变。逐路径取值表已写进 `docs/modules/bangumi.md`。
- **删除锁外快路径：响应不再可能落后于磁盘**：`_persist_bangumi_identity` 原先在锁外读一次状态，若看着"没变化"就直接用那份快照的 flag 提前返回。并发请求在这中间完成校验并在锁内写成 `true` 时，前一个请求会用过期快照回 `false`——磁盘 `true`、响应 `false`。`load_discovery_runtime_state` 每次从 JSON 重建独立对象，与回调内那份不是同一引用，所以两者确实会分叉。现在**总是进原子段**，合并与取值只在回调内发生。代价是重复上报同一身份多一次幂等重写（`update_json_state` 无条件写盘）：接受，因为不加锁判断"没变化"只能依赖一份随时可能被作废的快照。原并发测试用空字典强制进锁，恰好绕过了这条路径，已改为让锁外视图与锁内真值**故意不一致**来钉死它。
- **落库失败不再回传幻影状态**：上一轮"响应回传落库值"的说法在异常路径不成立——磁盘写失败、或运行时没有 memory manager 时，仍会回一个 `{"ok":true,...}` 携带没能存下的 flag，下一次读取直接打脸。现在响应体**整体读自实际写入的记录**；写不进去（异常或无 memory manager）则返回 **500** 并带可诊断文案（铁律 7），扩展本就把上报当 best-effort，下次 bgm.tv 页面访问会重报。三种情形各有测试：写异常 + 本轮成功、写异常 + 磁盘已有 `true`、无 memory manager。
- **占位符与括号回退两处解析修正**：(1) 从占位符表里去掉 `nil`——它是真实存在的日本摇滚乐队名，且 Python/JS/JSON 都产不出这个拼写（那是 Ruby/Lisp），本就不属于"我们会造的脏值"；音乐条目 `{"key":"艺术家","value":"nil"}` 此前会被抹成空。(2) 括号回退改为**同族配对**：`(credit]` 里的 `]` 不再无条件弹出 `(`，否则截断结果仍带未闭合的 `(`——上一条 commit 说明里"回退所有非零位置未闭合括号"因此并不成立。
- **旧版 `verified` 坏记录不再被继承，并在读取侧自愈**：上一版的 404 路径写下过 `{"uid":…,"username":"","verified":true}`。升级后再次上报同一个 404 用户，新校验正确得出 `false`，但 sticky 继承比较 uid 与 username 时两边的 username 都是 `""`、判定为「同一身份」，于是继承了旧的 `true`——这条记录违反刚立的「`verified` ⟹ username 非空」。现在继承额外要求旧记录在现行规则下合法（username 非空）；同时 `_load_bangumi_identity` 与 CLI 的 `_load_extension_bangumi_identity` 在**读取**时把「空用户名 + `verified:true`」归一为未确认，因此不依赖用户再次访问 bgm.tv 触发覆盖写。
- **纯标点/符号的作者名不再被当作占位符丢弃**：`・・・・・・・・・`（全部 U+30FB）是真实偶像组合名，`!!!` 是真实乐队（chk chk chk），「真实名字至少含一个字母或数字」这条判据把两者都清空了——而更粗糙的旧手写表反而保住了它们。这是过滤范围第三次放大、第三次删掉真实数据（前两次：手写表漏 `…`；为多抓 null 加入 `nil` 而误删同名日本摇滚乐队）。代价不对等：纯标点作者名只是卡片观感怪，删错真实艺名是数据丢失。因此过滤范围收回到**本栈真能产出的 null 字面量**（`none`/`null`/`undefined`/`nan`，对应 `str(None)`、JSON/JS `null` 与 `undefined`、`float('nan')`），标点与符号一律不再判断；`n/a`/`(none)`/`<none>` 同步移出（编辑散文，与已保留的 `无`/`未知` 同类）。三次误杀的经过写进代码注释，作为「不要再加判据」的负面知识。
- **令牌入口补上「为什么填」和「不填会怎样」**：五处会问 Bangumi 令牌的界面（setup 引导页、桌面 Web 设置页与初始化面板、扩展 popup 设置页与初始化面板）此前只说「约 1 年有效，视同密码保管」，看不出这东西值不值得去弄，也没说清令牌 / 公开用户名 / 已登录 bgm.tv 让扩展识别是**三选一**——本轮修的准入 bug 正源于这层关系没讲明白。现在五处都加了同一句取舍说明：个人令牌最完整（自动识别当前登录账号，可读私密收藏）；公开用户名次之（只读公开收藏）；两者都留空时，只要浏览器已登录 bgm.tv，扩展会自动识别账号（只拿到账号名，可能未经校验）。popup 沿用其对话式语气，桌面 / setup 保持说明式。新增 `test_every_token_field_explains_the_three_ways_to_supply_an_account`：五处逐一断言点到三条腿及各自取舍（私密收藏 / 公开收藏 / 未经校验），任一处漏写即失败（铁律 5 的漂移防线）。移动 Web 没有平台源设置页，不在范围内。
- **「已存凭据但来源未启用」不再静默**：真机实测发现的空档——用户在设置页粘贴个人令牌，后端经 `/v0/me` 校验通过并把用户名回填成 `215952`（一路都像"配好了"），但没勾启用开关；此时 `/api/sources/status` 只回 `state=disabled` + `detail="Bangumi 来源未启用。"`，`token_state` 还因为 `bangumi_source_status` 在 `if not enabled` 处**早于** token 计算就 return 而恒为 `""`，于是三端都拿不到任何线索，用户以为只差等待。现在 disabled 分支同样计算并下发 `token_state`，`detail` 点名已存的是哪种凭据、它当前不会被使用、以及只差哪一步：有令牌 → 「已保存个人令牌，但它现在不会被使用；把 Bangumi 来源开关切到「启用」并保存后才会生效。」，只填公开用户名 → 同句式（`token_state` 仍缺省，用户名不是令牌），令牌已被拒 → 指向重新生成并同时提醒启用，两者皆无 → 保持原文案不变。**`state` 语义不动**（仍是 `disabled`，它跟踪的是发现运行状况），所以桌面 Web 与扩展 popup 依旧按中性灰渲染"来源未启用"，只有 `token_state=rejected` 才走既有红色警示——待启用不是出错。**零前端改动**：两处渲染器本就逐字显示后端 `detail`，把话说清楚的责任留在后端，不给三端新增 per-platform 分支（铁律 5）。文案刻意不含"未启用"三字——两个渲染器都会自己前置状态标签（popup 还额外加 `(未启用)` 前缀），重复写会让同一行出现三次"未启用"，该约束由测试钉住。
- **新增取令牌分步文档，UI 只链过去**：`docs/modules/bangumi.md` 新增「获取 Bangumi 个人令牌」一节——先登录 bgm.tv、在 demo 页生成、令牌只完整显示一次、约 1 年有效、视同密码不要外传，以及过期后的实际表现（保存时经 `/v0/me` 当场拒绝；已保存令牌被拒则降级匿名并置 `token_state=rejected`，桌面与 popup 状态区显示「令牌已失效」）。**刻意不把外部页面的操作步骤写进 UI**：那是 bgm.tv 自己的页面，改版后写死在界面里的说明会过时且误导，因此文档里注明「以 bgm.tv 实际页面为准」，UI 只放一个「取令牌步骤」链接。文档标题上方留了注释说明五处 UI 链接该锚点，测试同时校验锚点存在与该节覆盖前置条件 / 有效期 / 失效表现。
- **海外出网默认值 `direct` → `system`**：`[network].mode` 的**内置默认值**改为 `system`。本节列的全是纯海外服务（LLM SDK、YouTube、Bangumi、GitHub 更新器、Codex OAuth），`direct` 下国内网络必然超时，而这是开箱默认值——新用户配好令牌、启用来源，然后撞上一句没头没尾的网络错误，机器上明明已有可用代理。`system` 即 `trust_env=True`，读环境变量 `HTTP(S)_PROXY`，macOS 上还读系统偏好设置里的代理；**没配代理时 `system` 与直连完全等价**，所以海外用户零影响。**只有「从没写过」才吃新默认值**：`_build_network_config` 按 `mode` **键是否存在**判定，不看解析后的值——显式 `mode = "direct"`（含 `OPENBILICLAW_NETWORK_MODE=direct`，env override 注入的是同一张表）照旧直连，因此凡是通过设置页保存过配置的用户磁盘上已有显式 `mode`，升级后行为一律不变；受益的是全新安装与从未配置过 `[network]` 的老配置。非法值（未知模式、`custom` 但 `proxy` 为空）仍回退 `direct` 而非新默认值——用户确实写了东西，不该因为写错就悄悄开始继承环境代理。**国内直连隔离不受影响**：B站 / 抖音 / 小红书 / 知乎 / Ollama / localhost / 国内 CDN 图片全部硬编码 `trust_env=False`，从不读取该策略；国内大模型网关豁免同样按 endpoint 生效。`tests/test_network_proxy_isolation.py` 新增 `system` 模式下的隔离守卫——此前的守卫只钉 `custom`（泄漏表现为多出一个代理 URL），而 `system` 的泄漏更安静：不出现任何 URL，客户端只是翻成 `trust_env=True` 开始听环境变量，既然这已是全新安装拿到的模式，就直接钉死它。`config.example.toml` 同步改为 `mode = "system"` 并写明理由（例子文件此前显式写 `direct`，照抄即绕过新默认值）。**「缺 mode 即 system」三条支路一并对齐**：`PUT /api/config` 与 `POST /api/config/probe-service` 收到只带 `proxy` 的 payload（旧版 UI、第三方客户端）时不再兜底 `direct`，与磁盘缺键走同一判定——非空 `proxy` 仍是 `custom`，清空则落 `system`；探测按真实策略跑，不再报告一个运行时根本不会用的 `direct`。`create_app` 镜像配置到进程级策略时的 `getattr` 兜底同样改为 `system`：残缺 config 对象没有可尊重的用户值，属「从没配过」而非「写错了」，且 `direct` 并非中性选项（它主动 `trust_env=False` 覆盖用户环境，`system` 才是顺从），配置坏到说不出偏好时顺从优于覆盖，也避免已经降级的启动再多长一个查不出来的超时。`network.py` 模块级 `_outbound_mode = "direct"` **刻意不动**并加注释说明：那是 import 到首次 `set_outbound_proxy` 之间的预初始化哨兵（两个入口都在 `load_config()` 后、任何消费者构造前就镜像，窗口内无人发请求），不是配置默认值，且与 `set_outbound_proxy` 的 URL-only 契约（空 URL ⇒ direct）一致。
- **推荐卡片不再把非 B 站的内容 ID 叫「BV号」**：`bvid` 是通用标识列而非 B 站专属列——`bangumi_subject_to_content` 把 subject id 存进 `bvid`，于是 `openbiliclaw recommend` 对 Bangumi 条目打印 `BV号  8`（8 是 bgm subject id）。改为与作者行同款处理（`_content_id_row`）：bilibili 出「BV号」，其它来源出「内容 ID」，缺省 / 未知 `source_platform` 回落 bilibili 以保住存量数据的原标签。
- **需要海外出网的来源，在配置页直接说出来**：改默认值救不了显式配了 `mode = "direct"` 的用户，他们照旧配好令牌、启用来源、撞上一句不提代理的网络错误。现在 `GET /api/sources/status` 的每个来源多带 `requires_overseas_network` 与 `network_hint`，后者是**后端写好的成品文案**，只在 `mode = direct` 时非空；桌面 Web 设置页与扩展 popup 设置页各自只做一件事——非空且该来源已启用就原样渲染成一条警示，**两端都不认识任何平台名，也不自己读 `[network].mode`**，加一个海外平台是改后端一行。海外桶经逐平台核实为 bangumi / youtube / twitter / reddit，但只有 bangumi / youtube 的出网真由 `[network].mode` 掌管（X 走 `twitter_cli`+curl_cffi、Reddit 走 `rdt`/OpenCLI 子进程或浏览器插件，而 `direct` 从不清 `HTTP(S)_PROXY`），所以后两者拿到的是「改这个设置修不好它，请确认系统代理本身可达」的措辞，而不是一句修不好问题的建议（铁律 7）。CLI 侧不另写一份判断：提示直接挂在 `BangumiClient` 的失败点上，`discover-bangumi{,-ranked,-latest}` 三条 smoke 与 API / discovery 链路一起受益。设置页里「仅作用于海外 AI 服务 / YouTube / 更新检查」那句同时改掉——它漏了 Bangumi，正是「读完整个网络设置仍不知道自己的来源连不上」的来源。新增 `tests/test_source_network_hints.py` 防漂移：清单只有后端一处、两端不含平台名分支、文案零副本。
- **Bangumi 正式接入平台来源契约体系（第 8 个 provider）**：合入时它走的是过渡态——`_bangumi_status_item` 以 `auth=None` 进状态端点，所以设置页没有「测试连接」按钮、没有证据徽章，`POST /api/sources/bangumi/verify` 还 404。现在它有了真契约。**它打破了 `auth_required` 布尔的隐含假设**：前七个平台要么必须登录、要么（YouTube）无凭据可验，而 Bangumi 是第三种——公开收藏 / 排行**匿名即可发现**，但配了个人令牌就能验证令牌。解法：`auth_required` **恒为 `False`**（你从不「需要」登录 Bangumi，无令牌时就是 YouTube 的形状、零告警，满足「匿名可读是正常状态」）；配了令牌时 `credential=present` + `verify_method=live_probe` + `can_verify_now=true`，`verification` 读共享探针缓存的 `GET /v0/me` 结论——从未验→`unverified`，令牌被拒（`unauthorized`）→`failed`，通过→`verified`，网络/超时/限流→`unverified`（indeterminate，绝不 `failed`）。出网走 `outbound_httpx_kwargs()` 代理策略，本机自定义代理到不了 api.bgm.tv 时如实报 indeterminate 而非误判令牌失效。控制实验（§0.1 / I3）：2026-07-19 实测 `/v0/me` 真令牌→`username='215952'`、伪造/无令牌→`unauthorized`，判据是两组之间有差异，故 `live_probe` 名副其实。`legacy.py` 一致性检查放宽一处——原「`auth_required=false` 不得带 live 方法」收紧成「且 `credential='none'` 才禁止」，可选凭据验证是诚实而非过度声称，YouTube 的过度声称仍被拦。`CredentialSpec` 新增 `opaque_credential`，把整串令牌（而非 cookie 字段名）作为探针缓存指纹。令牌写入仍走 config / init 表单（`kinds=()` + `form_kind='none'`，表单只给「测试连接」和「去获取令牌」链接），不经统一 `/credential` 端点。**如实记录的契约缺口**：因 `auth_required=false`，前端把 Bangumi 渲染成「无需登录」并抑制证据徽章，所以令牌的 `verified`/`failed` 虽如实写进契约字段，却不会以常驻 ◆ 联网验证 徽章出现——令牌结论目前经「测试连接」消息与 `token_state` 徽章暴露；要把它做成常驻徽章需给契约加一档「可选凭据」并改前端，本次零前端改动故未做。**零前端改动**：共享模块早已认 Bangumi，补上契约后徽章与按钮自动出现。新增 4 个冻结 case（无令牌 / 有令牌未验证 / 令牌已验证 / 令牌+停用）与四条 verify 复现（有效→verified、伪造→failed、无令牌→indeterminate、network_error→indeterminate），冻结断言新增 `token_state` 轴。

---

## v0.3.174：Bangumi 平台来源（2026-07-18）

- **新增第八个正式内容来源 Bangumi**：使用官方 `v0` API 匿名只读直连，`BangumiDiscoveryProducer` 以 `search / ranked / latest` 三分支、逐日预算、cursor、最小间隔和 `Retry-After` 冷却接入统一关键词与 `discovery_candidates → shared LLM eval/admission → content_cache` 主链；`sort=date` 在三端明确显示为“按日期浏览（可能含未播条目）”。用户显式提供公开用户名后，可把公开收藏转换为统一事件参与 guided init；Bangumi-only 初始化缺用户名会在预留 run 前拒绝，混合来源则仅跳过该画像分支并返回 warning。
- **目录评分不再冒充社交互动**：新增通用 `rating_score / rating_count / source_rank`，与 Bangumi 真实收藏人数一起贯穿 `DiscoveredContent`、待评估池、正式缓存、推荐/惊喜 API、桌面/移动/扩展卡片；排名按原始序号 `#N` 展示，不套互动人数的万/亿缩写。canonical identity、点击 URL 与保存 identity 均识别 `bangumi/bgm`、`bgm.tv/subject/<id>` 与 `bangumi.tv/subject/<id>`，封面代理白名单加入 `lain.bgm.tv`。
- **三端配置、状态和只读诊断齐备**：`[sources.bangumi]` 支持开关、公开用户名、条目类型、分支预算、节流和 bootstrap 上限，候选池 share 默认 `1`；桌面 Web、扩展设置/初始化与 packaged setup 同步接入。新增 `fetch-bangumi`、`discover-bangumi`、`discover-bangumi-ranked`、`discover-bangumi-latest` 只读 smoke，以及正式 `discover --source bangumi [--force]`；状态页纯本地读取，不因打开设置访问上游。匿名基础路径不收 Cookie、不调用 Bangumi 站内写接口（可选令牌与扩展身份识别见下方条目）。设计与验收见 `docs/plans/2026-07-17-bangumi-source-{spec,plan}.md`，模块说明见 `docs/modules/bangumi.md`。
- **Fable review 收口运行边界**：显式 `discover --source bangumi` 不再被后台 scheduler 总开关误判为 disabled，并补全 disabled/no-profile 指引；每日预算改为跨分支去重与最终 limit 后按实际保留候选扣账，空白 keyword claim 直接标 failed，429 则 rollback；本地状态读取不再为 cooldown 临时构造 producer，匿名来源的 legacy `logged_in` 改为表达本地 ready 状态而非简单复制 enabled。复核进一步补上 browse total 缩小时超界 cursor 的 400 自愈、搜索第二词限流时保留首词候选、small-limit 下 subject type 持久化轮转、guided init 显式空用户名覆盖旧配置且三端草稿保留、设置页完整五类型，以及目录评分字段仅在非零时进入 evaluator prompt，避免改变存量平台模型输入。
- **新增 Bangumi 个人令牌（Personal Access Token）认证通道**：`[sources.bangumi].access_token`（生成地址 https://next.bgm.tv/demo/access-token ，约 1 年有效，视同密码保管，日志只记存在与否/长度）。提供令牌后，guided init / `fetch-bangumi` 经 `GET /v0/me` 自动识别"你是谁"并以 `Authorization: Bearer` 读取本人收藏（含私密）；显式用户名与 `/v0/me` 不一致时以 `/v0/me` 为准并告警。令牌经 `/v0/me` 校验通过后才写入 config（坏/过期令牌当场拒绝并回传真因），无令牌时匿名公开用户名老路径完全不变。CLI 新增 `init --bangumi-token`、`fetch-bangumi --token`；guided init `source_options.bangumi.access_token` 白名单打通扩展 popup、桌面 Web、packaged setup 三个 GUI surface（含错误文案映射），令牌通道满足完整 four-surface 契约。后台发现链路的 token 在遇 401/403 时降级为匿名公开发现并给出清晰诊断，绝不静默吞掉。`trust_env=False` 与只读边界保持不变。
- **新增 Bangumi 扩展自动识别通道（零配置主推路径）**：浏览器扩展新增 `*://*.bgm.tv/*`、`*://*.bangumi.tv/*` host permission 与两段内容脚本（**Chrome 商店权限披露需同步更新**）——MAIN-world 桥读取页面公开 `CHOBITS_UID`（>0 即已登录；登出绝不上报），isolated 脚本从导航栏 `/user/<username>` 链接解析用户名，经 `POST /api/sources/bangumi/identity` 持久化到 `discovery_runtime_state["bangumi_self_info"]`（非正整数 uid 422 拒绝、非法用户名当缺失）。guided init 与 CLI init 的账号解析统一为三级优先：令牌 `/v0/me` > 显式/已配置用户名 > 扩展上报用户名 > 报错，命中扩展身份时明示来源。真机 E2E 抓出并修复一处 plausible-but-wrong：泛化 avatar 兜底选择器会在匿名首页把时间线路人用户名当成本人——client 侧删除泛化兜底只留本人专属导航区，后端持久化前经匿名 `GET /v0/users/{username}` 权威比对 `id == uid`（不一致/不存在 → 只存 uid 丢弃用户名并 WARNING；网络失败 → best-effort 接受待下次复校；实测未设自定义 slug 的用户 `username == str(uid)`，uid-only 上报亦可解析）。Bangumi 内容脚本只做身份识别，不采集浏览行为、不碰 Cookie；uid/用户名均为公开资料。
- **个人令牌可在设置页配置（补齐初始化外的入口）**：桌面 Web 与扩展 popup 设置页新增 Bangumi「个人令牌」password 输入框 + `生成个人令牌` 链接，此前只有初始化页能填令牌，用户初始化后想开启私密收藏只能改 `config.toml`。GET `/api/config` 新增 `access_token_set` 布尔（只报是否已配置，绝不回传明文）；设置页据此显示「已配置（留空保持不变）」占位，仅当用户实际输入新令牌时才发送 `access_token`，留空保存不会误清空已存令牌。凭据状态卡文案同步更正（不再宣称"不保存 token"）。四端令牌入口（init popup/setup/桌面 + 设置页桌面/popup）齐备。
- **Fable review 二轮收口**：`_subject_tags` 对非列表 `meta_tags`（schema 漂移的裸字符串/字典/标量）不再逐字符拆分，仅遍历真正的数组；`BangumiDiscoveryProducer` 在所有启用分支都因当日预算耗尽而完全未发起请求时返回顶层 `reason=budget_exhausted`（区别于真正跑通却为空的 `empty`），`mode_results` 逐分支如实记账，`discover-bangumi`/正式 discover 文案随之改为"今日预算已用完"。扩展 popup、桌面 Web 与打包 setup 三端 guided-init 统一 omit-vs-clear：仅当用户手动编辑、或在成功 `/api/config` prefill 后显式清空用户名时才发送 `username`（清空即发 `""` 覆盖配置），prefill 失败/未完成/字段从未触碰则省略该字段，避免用空值误删已配置的 Bangumi 用户名；三端同时读取并按现有状态/提示样式安全渲染 `/api/init` 202 的 `warnings`（如未填用户名的 discovery-only 提示），不再静默丢弃。`fetch_bangumi_public_collection_events` 对正常 bootstrap 按 50 行请求、较小全局 `limit` 不超过目标量，并按 lane 缓存富余行复用；在保持 per-scope 公平份额、去重、限速、终止与不过量导入的前提下，用较大的缓冲分页替代默认 `per_pair=20` 造成的大量小页请求。
- **令牌拒绝状态持久化 + 设置页保存 /v0/me 校验 + 清除入口（令牌登录态可见性三缺口）**：(A) 发现链路 401/403 降级时把拒绝标记持久化到 `bangumi_discovery_state`（`token_rejected` 行，`note` 存令牌 SHA-256 前 12 位**指纹**+ISO 时间戳，绝不存明文），重启后同一令牌（指纹未变）直接走匿名不再重复吃 401，换新令牌先试用、成功即清标记；`/api/sources/status` 新增 `token_state`（`ok`/`rejected`/未配置缺省），`rejected` 时 detail 明写"个人令牌已被拒绝（可能过期）…请重新生成"，桌面 Web 与扩展 popup 状态区渲染红点/"令牌已失效"，凭据卡追加失效提示。(B) `PUT /api/config` 收到新的非 masked 非空 `access_token` 时镜像 init 语义经 `/v0/me` 校验——401→400 `invalid_bangumi_access_token`、网络/上游失败→502 `bangumi_token_check_failed`，绝不静默接受坏令牌；成功才写入令牌+`/v0/me` 用户名并清除拒绝标记；masked echo/省略 key/其它配置保存零网络。(C) 桌面设置页与扩展 popup 设置页各加「清除已保存的令牌」勾选控件，勾选后本次保存显式发送 `access_token:""` 清空令牌并清除拒绝标记，不破坏"留空=保持不变"语义。指纹只在本地 SQLite，不涉及明文，隐私边界与商店披露不变。
- **审计缺口收尾——文档披露、init 放行与封面路由**：主页 `docs/index.html`（CN/EN i18n 三处）、Chrome 商店 listing 与隐私政策同步 bgm.tv/bangumi.tv host permission 真相——扩展仅做账号身份识别（读公开 uid + 用户名），不读 Cookie、不采集浏览行为、不传令牌（既有"不新增 host permission / 不保存 token"文案已纠正）；init 写保护 allowlist 精确放行 `POST /api/sources/bangumi/identity`，让 guided init 当轮就能拿到扩展刚上报的身份（三级账号解析最需要它的时刻）；实测（2026-07-18 curl）确认 `lain.bgm.tv` 为 Cloudflare 海外 CDN，直连超时而系统代理 200，属 ytimg 走代理模式而非 CN 风控模式，故封面保持 `trust_env`（不加入 `_DIRECT_FETCH_HOST_SUFFIXES`），结论记入代码注释与测试。
- **端到端盘点补口**：`fetch-bangumi --rebuild-profile` 不再走 B 站认证门（画像重建只吃 Bangumi 事件，非交互终端不再被"请先 auth login"卡死——`_prepare_init_runtime(require_bili_auth=False)`）；收藏事件语义补全——metadata 新增可读 `subject_type_label`（动画/书籍/游戏/音乐/三次元）并把 subject 的 `meta_tags`（TV/剧场版等，防 schema drift）透传；`docs/modules/config.md` 与 `config.example.toml` 的 `[network]` 作用范围补记 Bangumi 海外服务需代理，`docs/modules/bangumi.md` 显著位置加网络要求并记录作者字段恒空、delight 惊喜信号不适配 bangumi 两条已知限制；补齐 `/api/feedback`·`/api/saved`·聊一聊 bangumi 端到端断言与 PreferenceAnalyzer 满意度过滤真实测试。
- **删除死代码 `DelightScorer`（推荐输出零变化）**：`recommendation/delight.py` 里的 embedding 多信号打分器（`deep_need_alignment` / `insight_resonance` / `likes_alignment` / `novelty_factor` / `quality_indicator` / `exploration_match` / `dislike_penalty` 加权）自被 Evo 复用方案取代后就再无生产调用点——全仓 `DelightScorer(` 实例化 0 处（仅单测），`score()` / `_build_reason_stub()` / `_quality_indicator()` 等在模块外 0 引用，生产代码从该模块只 import `effective_delight_threshold` 与 `DEFAULT_DELIGHT_THRESHOLD`。真实评分路径是 `precompute_delight_scores()` 复用 Evo 写入的 `relevance_score`（有意省掉一次 LLM 调用），目录评分 `rating_score / rating_count / source_rank` 则经 `_prompt_visible_content_fields` 进入共享 evaluator prompt 由 LLM 在语境中权衡。本次一并删除只服务于它的 `DelightSignals` / `DelightWeights` / `SupportsDelightCandidate` / `SupportsRecommendationSignalStore` 与失效的 `_DEFAULT_WEIGHTS`，并修正 `llm/embedding.py` 中指向该类的空缓存守卫注释。**保留**阈值口径 `DEFAULT_DELIGHT_THRESHOLD` / `CONSERVATIVE_DELIGHT_THRESHOLD` / `effective_delight_threshold()` 及其标定注释，所有生产 import 与阈值取值逐字节不变——**本次改动不改变任何推荐输出、API 响应或 CLI 输出**。`tests/test_delight_scorer.py` 中打分器用例删除，阈值用例改为直接测模块级 `effective_delight_threshold()`，其余 delight 存储层用例原样保留，并新增防复活断言锁定「delight 模块只暴露阈值口径」。

## v0.3.173 / extension v0.3.173 / desktop v0.3.173：自动更新链路全面加固（2026-07-16）

后端源码走 `backend-v0.3.173`，浏览器插件走 `extension-v0.3.173`，桌面安装包走 `desktop-v0.3.173`。三渠道版本重新对齐（0.3.172 因未发 extension tag 导致聚合校验失败、Latest 页面短暂空资产,已降级为 prerelease;本版为所有渠道的推荐升级目标）。

- **抖音 discovery 出货量修复（claim/search 对齐、热词轮换、search API 快速失败）**：四处协同解决抖音 80 分钟仅约 3 条 vs B站 149 条的低出货。(1) **claim/search 对齐**——producer 每轮 `claim(n=keywords_per_run=3)`，与策略实际搜索的关键词数一致，不再 claim 5 个只搜 1 个、把 4 个未搜索的词误 `mark_used` 烧掉。(2) **节流减半**——`min_interval_minutes` 30→15，80 分钟内的 search 轮次翻倍。(3) **热词轮换**——`DouyinPluginSearchClient` 用进程内 `sentence_id → 单调时钟` 表（TTL 6 小时，基于 2574280 连播 3 轮只出重复的日志证据校准）过滤近期已用热词后再截断到种子数，全部近期时回退陈旧词（宁可陈旧不要空手），避免连续 hot 轮反复挑同一个 top 热词只出被 dedupe 吸收的重复。(4) **扩展 search API 快速失败**——`harvestSearchViaApi` 的每次 `w.fetch` 加 `AbortController` + 15s 超时（`search/single` 缺 a_bogus 时风控会永久挂住连接），让卡死的路径快速失败切换到下一路径，而非每关键词任务空耗约 50s 撞上 45s 内容脚本桥接超时（`harvestHotRelatedViaApi` 不动，该端点正常工作）。a_bogus 签名重写不在本次范围内。
- **抖音 search 升级为被动优先分页采集**：不重写 a_bogus——扩展触发真实 UI 搜索后，页面自身会发带完整签名的 `search/single` 请求，被动 fetch-tap 直接收割。`runSearch` 改为滚动**真实结果容器**（`pickSearchScrollTarget` 从 `/video/` 锚点向上找最近的可滚动祖先，找不到才回退 window 滚动）驱动页面自发翻页；固定 4 轮 × 1s 睡眠替换为自适应循环（上限 10 轮、按去重后计数连续 2 轮无增长即停、每轮 250ms 轮询增长最多 3s、增长即中断等待）。被动采集从副产品变为一等信道并进入遥测：debug 新增 `passive_items_harvested` 与 `scroll_rounds`，消除此前 `videos=15, dom=0, api=0` 的诊断困惑。API bridge 兜底保持原样（15s 快速失败）。
- **修复 B 站取消点赞被误记为第二次正向点赞**：B 站按钮用 class `on` 表示选中态而没有 `aria-pressed`，DOM kernel 无法识别撤销；既有 MAIN-world `bili-interact-tap` 现按网络写入权威采集点赞（`like=1/2`）、收藏增删与投币，仅 HTTP 2xx 且业务 `code===0` 才发事件，取消赞 / 取消收藏归一为 `feedback` retraction（`signal_strength=0.2`）。B 站 adapter 同步将 `{like,favorite,coin,retraction}` 纳入 tap 权威集合，DOM 对这些动作零发射，避免双计；端点 fixture 依据 bilibili-API-collect 公开记录构造，仍待真机验证。
- **源码自动更新链路按 effective URL、强 TLS 与进程唯一写入全面加固**：remote 守卫同时校验 `ls-remote --get-url` 和全部 `get-url --all` 值，放行 GitHub 官方 SSH-over-443、拒绝 `insteadOf` 镜像改写与凭据地址且绝不改写用户 git 配置；API 传输失败会尝试 Atom，空异常、畸形 JSON、git 缺失/超时、lockfile checkout 与 `index.lock` merge 都保留稳定 reason 和真实 `last_error`。更新器删除 `verify=False` 降级，staged 改动算脏但未跟踪文件仍豁免，tag 通道在 git 变更前封闭，apply 锁跨配置热重载保持进程唯一；custom 代理显式贯通 git/uv/pip，direct/system 继承行为不变。检查间隔加载时保证至少 1 小时、保存时拒绝非法值；桌面 error 卡优先显示明细，扩展 popup 对 frozen/docker/unsupported 禁用自动应用，含空格仓库路径的修复命令统一加引号。prerelease 候选排序补齐 SemVer §11 语义（同号 stable > 任意 prerelease、`rc.10 > rc.9 > rc1`），UI 展示保留 prerelease 后缀。移动 Web 更新面板与 CLI update 命令明确维持现状，未在本次范围内。规格与实施计划见 `docs/plans/2026-07-16-auto-update-hardening-{spec,plan}.md`（基于 Codex 全链路审计 13 项发现的裁决）。

- **平台来源接入契约统一：一个绿灯只有一种含义（Wave A + Wave B Task 8/9）**：`/api/sources/status` 此前用一个 `state` 字段承载四个正交维度，同一个 `logged_in=true` 背后是六种强度迥异的证据——B 站只是数出 cookie 串里有三个字段名（不联网）、小红书/知乎是浏览器 72h 心跳、Reddit 是本地文件未超 7 天（注释明说绝不联网）、X 是上次请求没报 401、YouTube 是硬编码常量，而抖音**即使 cookie 完全有效也永远显示「状态待验证」**。四个平台在设置页并排显示同一个「凭据已就绪」，用户无从分辨"真能用"与"只是填了值"。(1) **契约正交化**——新增 `SourceAuthContract`（`auth_required` / `credential` / `credential_origin` / `verification` / `verify_method` / `verify_ttl_seconds` / `verified_at` / `can_verify_now`），其中 `verify_method`（`live_probe` > `passive_health` > `browser_heartbeat` > `local_file` > `task_history` > `none`）如实标注结论的证据强度；`sources_status()` 从 **424 行**巨型 if/elif 拆成 `api/source_auth/providers.py` 的 7 个纯函数聚合器（**38 行**），`SourceAuthContext` 刻意只持有 config 与 database、拿不到 HTTP client，使"状态端点绝不出网"由作用域强制而非 review 自律。旧 `state`/`logged_in` **逐字节零变化**（原样承袭而非推导——bilibili 与 douyin 正交字段完全相同却对应 `ready/True` 与 `unverified/False`，证明旧值不可推导，这一不可能性本身即诊断 D1 的最强证据），改由 `check_legacy_consistency()` 断言两套视图互不矛盾。(2) **抖音状态误报修复**——`api/app.py` 原 docstring 断言抖音"没有稳定 nav 端点能区分未登录与软风控"，经剥离对照实验（实验组=完整 cookie，对照组=剥掉 12 个登录 cookie 的游客态，同签名器/UA/时刻）推翻：`/aweme/v1/web/user/profile/self/` 已登录返回 `status_code=0`+非空 uid、未登录返回 `status_code=8` "用户未登录"，实测延迟均值 329ms。注意 `/aweme/v1/web/query/user/` 两组返回**相同**的设备级 uid，只看"有没有 uid"会误判，探针与测试均对此设防。(3) **`POST /api/sources/{slug}/verify`**——7/7 平台一键验证，按固定动作表分派（**不按 `verify_method`**：后者随状态变化，知乎无心跳时回落 `task_history`，照它分派会让知乎在最需要验证时反而无可执行动作）；`outcome`（这次点击验证到了什么）与 `auth.verification`（我们现在相信什么）严格分离，避免渲染出绿色「已验证」配「插件未连接」；三态而非两态，探测超时/插件未回/平台限流/YouTube 无需登录一律 `indeterminate`，绝不显示成「凭据失效」诱使用户删掉好 cookie；每平台 10s 去抖 + in-flight 标记，防止连点变成自造风控。B 站原有的两条各自缓存的活体验证路径（`init_prereqs` 喂 `/api/init-status`、状态端点各一份）合并为进程级单一 store，根除两界面对同一 cookie 给出相反结论的可能。(4) **修 B 站凭据双读取路径**——`runtime/init_prereqs.py` 此前只读 config.toml，而 CLI `auth login` 只写 `data/bilibili_cookie.json`，导致同一份凭据下引导页报「未登录」、设置页报「已就绪」；现统一走 `resolve_runtime_cookie()`。(5) **凭据写入归一**——新增 `POST /api/sources/{slug}/credential`（结构校验 → 活体校验 → 落盘 → 广播 → 返回重算后的契约），7 条老端点保留为响应结构逐字段冻结的 `deprecated=True` 转发；**`PUT /api/config` 一并纳入**（它路径叶子是 `config`、按词根扫描看不见，却一条路由写四个平台凭据，且设置页手工粘贴走的正是这条路），四处凭据写入改为委托同一校验门，消除「同一份无效 cookie 经 POST 被拒、经 PUT 静默落盘」的矛盾；无效凭据经磁盘/DB 断言确认从不落盘；传输失败**拒绝**而非存入未校验 cookie。**架构上无法校验的绝不伪造**——xhs/zhihu 只存一个 bool、后端零字节 cookie，其写入显式返回 `checked="none"` + `unverified_reason`。(6) **三端「测试连接」按钮**（桌面 Web + 插件 popup，复用既有 `renderProbePending`/`renderProbeResult` DOM 约定并扩展为三态 tone，`neutral` 特意不用灰色以免"判定不了"被读成"仍在探测"）。新增 `scripts/source_contract_metrics.py` 作为 CI 量化门（聚合器行数 424→38、有 verify 动作的平台 0→7、凭据写入端点命名形态 4→1）。规格与实施计划见 `docs/plans/2026-07-18-source-auth-contract-{spec,plan}.md`；新平台接入的强制契约写入 `docs/platform-source-integration.md` §0.1–§0.4，其中明确规定**声称某平台"无法验证"之前必须先做剥离对照实验**，拿不出对照数据不许写进 docstring。
- **平台源设置改为后端描述符驱动，三端共享同一份渲染（Wave B Task 10/11）**：接着上一条，把最后一份手抄副本也收掉。(1) **表单描述符下发**——`GET /api/sources/credentials` 每项新增 `form`（`kind` / `label` / `placeholder` / `env_var` / `required_keys` / `required_keys_mode` / `actions` / `help_text`）与 `summary`，全部由 `CREDENTIAL_SPECS` **派生**而非另写一份，表单声称的必填 cookie 名与写入校验门永远同源。`kind` 是能力声明：xhs/zhihu 为 `extension_only`，后端一个字节的 cookie 都不存，因此三端**不得渲染可粘贴输入框**（给能填的框是让用户往虚空里打字），但保留 `verify` 与 `open_login_window`——「去浏览器登录」才是这两个平台唯一有效的修法；youtube 为 `none`。`required_keys_mode` 是 spec 字段表之外补的：抖音三个 session cookie 是**任选其一**，平铺成 `required_keys` 会让 UI 声称校验器要求它其实不要的东西。spec 举例的 `clear` **故意未实现**——全 API 没有端点能抹掉已存凭据（`PUT /api/config` 空字段意为「本次没编辑」，恰恰相反），先挂按钮后补端点就是 UI 开始说谎。(2) **三端共享渲染模块**——新增 `src/openbiliclaw/web/shared/source-status.js`（独立 `/shared` mount，因为 `/web` 挂的是 `web/desktop/`，`web/shared/` 从 `/web/shared/` 根本取不到），桌面 Web、插件 side panel、setup 引导页全部加载它；插件走 build 期复制（MV3 CSP `script-src 'self'` 禁止从后端拉脚本，产物已 gitignore，提交它就变第四份副本）。删除桌面 `SOURCE_ACCESS_STATE`、插件 `SOURCE_STATUS_DOT`/`SOURCE_STATUS_LABEL`，以及两端各一份的 `VERIFY_OUTCOME_TONE` / `renderVerifyResult` / `startSourceVerifyCooldown` 与三份 7 平台名单。**修掉两个用户可见的漂移**：插件此前把 `no_auth` 与 `unverified` 都画成同一个灰点（`#9aa0a6`），于是「这个源不需要登录」和「这个源状态不明」长得一模一样——两个状态后端都真的会发（YouTube 的 `no_auth`、xhs/douyin/reddit 的 `unverified`）；现在 `unverified` 走 `pending` 蓝（`#3898ec`，取自桌面 `--source-pending`），真机截图确认两端色调与文案逐字一致。未知状态兜底也从「桌面显示『状态未知』、插件显示**空字符串**」（一个没有任何说明的灰点）统一为「状态未知」。引导页的 `checkBili` 从直接 string-test `config.bilibili.cookie`（看不见 data file 与环境变量里的 cookie）改为读同一份契约。**状态表刻意留在 JS 而不上收进契约**：后端发 `access_label`/`access_tone` 看着更「契约驱动」，但会留下 Python 一份 + JS 兜底一份、两种语言两份同一知识，而指标脚本只扫得到其中一份——那正是 spec I7 说的语法代理陷阱；文案实质（`detail` / `message` / `summary`）本来就全由后端下发。指标脚本第 2 项 1→0、第 3 项 2→1。**saved-sync 那一族的 6 份映射不合并**：判据是「这张表的键是不是 `/api/sources/*` 发出来的字段值」，它与本枚举共用 `login_required` / `rate_limited` 两个**拼写**，但回答的是「这一条收藏同步成功没有」而非「这个源接不接得上」，属另一个枚举（引导页 `INIT_REASON_TEXT` 同理）。新平台约定见 `docs/platform-source-integration.md` §0.5–§0.6。
- **接入状态改由正交契约驱动，证据强度终于可见（Wave B Task 12）**：上一条把契约铺到了后端与共享模块，但 `describeAccess()` 仍**只读 legacy 的 `item.state`**，全文件唯一消费 `auth` 的地方是 `hasCredential` 读 `auth.credential`——于是整个契约重构对用户不可见：抖音后端早已是 `verification=verified`，界面照旧显示「状态待验证」；B 站（`live_probe` 但尚未探测）与 Reddit（`local_file` 且已验证）依然并排显示同一个「凭据已就绪」，而「同一个绿灯背后强度不同」正是整个 spec 要解决的问题。现在标签与色调全部由 `auth` 派生，判定顺序 `auth_required=false` → `credential` → `verification`；凭据维度先于结论维度，是因为两者正交、可能互相矛盾，此时宁可少报也不点亮一盏兜不住的绿灯。**`verify_method` 升为独立的第二维度**，用三种方式同时编码且没有一种是颜色（色觉障碍用户读不到颜色）：字形（◆ 联网证据 / ◇ 本地或间接证据 / — 无验证能力）、中文方式名（联网验证 / 请求反馈 / 插件心跳 / 本地文件 / 历史任务）、边框（实线 / 虚线 / 点线）。本版本不认识的 `verify_method` 刻意渲染成**弱**而非强——猜一个没见过的方式「很硬」正是这套契约要消除的过度声称。文案命名的是**能力**而非结果（B 站在首次探针跑之前 `verify_method` 就已是 `live_probe`），结论跑没跑由后缀承担：`verified_at` 有值渲染「3 分钟前」，为空渲染「尚未验证」，TTL 概念因此对用户有了意义。`auth_required=false` 单独一档「无需登录」且不显示证据徽章——不需要凭据的源没有证据可评级，它既非已验证也非待验证。**修掉一个跨时区的时间 bug**：`verified_at` 有两种线格式，多数 provider 发带 `+00:00` 的 isoformat，而三处从 SQLite 读回时间戳的（X 的 `x_source_health`、知乎与 Reddit 的 `task_history`）发的是 `CURRENT_TIMESTAMP`——是 UTC 但不带时区标记，`Date.parse` 会当本地时间，UTC+8 用户看到的新鲜结论会凭空老 8 小时（方向还正好错：让真证据显得陈旧）；`normalizeTimestamp()` 仅在字符串确实不带时区时补 `Z`。`item.auth` 缺失或畸形时**优雅回退**到既有 `state` 表，装在插件商店里的 side panel 对着更老的自建后端仍渲染出真实 chip 而非空白。三端出口不同、数据同源：桌面页给证据独立徽章 `.source-evidence-badge[data-rank]`，侧边栏每源只有一行故用 `access.line` 内联括号（`已验证（◆ 联网验证 · 3 分钟前）：…`），setup 引导页的 B 站步骤从写死的「已检测到 B站 登录」改为打印同一份标签与证据。真机验证（隔离 `OPENBILICLAW_PROJECT_ROOT` + `HOME` 的一次性 8435 后端）构造出 B 站 / 小红书 / Reddit **legacy `state` 同为 `ready`** 的一帧，三者标签同为「已验证」、tone 同为绿色，但徽章分别是 `◆ 联网验证 · 刚刚`、`◇ 插件心跳 · 5 小时前`、`◇ 本地文件 · 2 天前`；抖音同帧显示 `已验证 ◆ 联网验证`，即 D11 那个「可修的误报」在 UI 上翻正。指标脚本第 2 项保持 0、第 3 项保持 1。
- **接入契约外部 review 修复：12 项后端缺陷，每项先有复现测试（Codex gpt-5.6 / reasoning=max）**：上面三条交付后做了一轮对抗式外部 review，12 项全部成立，共性是"看起来测过了但没测到点子上"——所以每项都先写一个复现触发场景的失败测试，红了再修。(1) **活体缓存击穿「无效凭据绝不落盘」（BLOCKER）**——写入门按 60 秒窗口复用正面结论时**只认平台不认凭据**，于是旧 cookie 验证成功后 60 秒内提交另一份结构完整却已失效的 cookie，会命中缓存直接放行并落盘，一个网络请求都不发。复用本身的理由成立（抖音 `msToken` 频繁轮换，插件每次启动重发整个 jar，每次都探测就是自造风控），故保留优化并加凭据同一性校验：`ProbeVerdict.credential_fingerprint` 存该平台**登录态字段**的 SHA-256（字段名直接取自 `CREDENTIAL_SPECS` 的校验门，所以"什么算同一份凭据"与"校验门要求什么"不可能漂移），`msToken` 不在其中因此轮换仍命中；写入门用严格的 `peek_matching()`（指纹不符或缺失一律重探——猜错的代价是死凭据落盘），状态端点用宽松的 `contradicts()`（只有明确不符才丢弃，缺失指纹仍显示，其暴露面被 60s TTL 兜住且自愈）。命中缓存的结论**不再重新记录**，否则每次插件重发都顺延自己的有效期，一份凭据可以永远"刚刚验证过"而实际从未复验。(2) **X 是伪验证（违反 I3）**——`x_source_health` 的行以 `state='ok'` 为**默认值**建出，"从未发过请求"与"上次请求成功"在 `state` 上完全同形，于是全新数据库首次写入一份从未用过、甚至早已过期的 X cookie，`/api/sources/status` 立刻宣称 `verification=verified` + `verify_method=passive_health`——而 `passive_health` 恰是唯一无法主动重跑自证的方式。新增 `last_success_at` 列（只由 `record_success` 写），无真实流量报 `unverified`；`clear_relogin_block()` 会**清空**它（凭新 cookie 给的乐观解封不是用新 cookie 拿到的结果）；迁移**不回填**，老行里没有任何信号能区分两种情况，猜一个就是把同一个伪造推迟一次迁移。(3) **`PUT /api/config` 事务顺序（BLOCKER）**——抖音/X/Reddit 的凭据在字段解析处就写盘，而 `[network]` 校验与保存锁在几百行之后，于是同时提交有效抖音 cookie 与非法 `network.mode=custom, proxy=""` 会返回 400「未写入」而 cookie 早已被覆盖，且这三个 store 既无快照也无回滚。四个平台的写入改为延迟闭包，在 `_CONFIG_SAVE_LOCK` 内、`save_config()` 成功之后执行；并发 PUT 也不再可能拼出"config 来自甲请求、凭据来自乙请求"。**改的是顺序不是位置**——持久化仍留在 handler,因为它正处在 config.toml 事务中。(4) **去抖窗口重放过期结论**——10 秒去抖按**平台**存结果,恰好撞上修复路径:验死 cookie（窗口以 `failed` 武装）→ 保存能用的 → 10 秒内再点验证 → 原样回放旧失败,用户读到"修了也没用",下一步多半是删掉那份真能用的 cookie。凭据落盘即 `note_credential_changed()` 清条目。(5) **PUT 路径丢弃成功探针的 verdict（违反 I5）**——它确实出网探测、确实拒绝了该拒绝的,然后把结论扔了,状态仍是 `unverified`;同一份 cookie 走 POST 却是 `verified`。两条写入路径改为共用 `_credential_landed()`,校验**强度**相等之外**结果**也相等。(6) **旧 `/api/bilibili/cookie` 响应字段退化**——缓存命中分支不带 `username`/`user_id`,装机扩展拿到 `authenticated=true` 配 `username="", user_id=0`;verdict 现随缓存条目一起存取。(7) **`except Exception` 漏掉 `CancelledError`**（3.8 起继承 `BaseException`）——前端 fetch 取消或上层超时会让 in-flight 标记残留至 60 秒上限,期间每次点击只回"正在进行中"而那次验证早已停止。(8) **legacy 一致性表键集错配**——表里收录了后端根本发不出的 `expired`（读起来像"过期这条覆盖了",而真正会发的 `missing_cookie`/`expired_cookie` 一个约束都没有）,同时 `rate_limited` 被钉死要求"有凭据",于是"限流后用户删掉 cookie"这个完全合法的状态被误报为契约违规。表按**后端实际发得出的 13 个状态**重建(**注意 spec D6 说的 11 个漏了 Reddit 的 `login_required`/`error`,两者均有冻结用例证明可达**),不约束的状态显式写成全集加注释而非留空——运行时二者等价,对读者不等价;并加测试把键集钉死在冻结用例证明可达的状态集上,死键与漏键都不可能再悄悄出现。(9) **冻结测试名不副实**——`test_sources_status_state_and_logged_in_are_frozen` 只断言 `(state, logged_in)`,而对外声称的是**五个 legacy 字段**逐字节冻结,`detail`/`enabled`/`feed_paused` 无任何保护;旧端点测试也只比对键集、不比对值(一个键还在、值被掏空的响应对它是隐形的)。五个字段全部纳入冻结,旧端点改为逐字段比对值。**变异验证**:悄悄改掉小红书一句 `detail`,新断言 2 个用例失败,旧的两字段断言 37 passed 全绿。(10) **`validate_live` 削弱承诺——判定为删**:全仓零调用方(扩展、三端前端、CLI 都不发),等于给任何能连上 localhost 的东西一个关掉端点核心承诺的官方途径,不换来任何好处;老端点 `validate_with_bilibili` 保留(装机扩展一直在发且恒为 `true`),但即便传 `false` 结构校验门仍跑,故现在可达的最弱校验也比过去强。(11) **抖音 `detail` 与 `verification` 自相矛盾**(前端真机截图暴露)——`detail` 是 D11 之前"抖音无法验证"年代写死的常量,探针接上后没人回头改,于是三端切到正交契约后同一张卡片同时写着「接入：已验证」「◆ 联网验证 · 刚刚」和「需在实际任务中验证」。所有测试都没抓到,因为 `detail` 只在"尚无结论"这一个状态下被冻结,而那恰好是老文案仍然正确的状态。B 站与抖音的 `detail` 现按 `verification` 查表,`unverified` 一档保留原字符串故冻结用例逐字节不变。(12) **`verified_at` 两种时间格式**——三处从 SQLite 读回的(X 的 `x_source_health`、知乎与 Reddit 的 `task_history`)发 `CURRENT_TIMESTAMP`,是 UTC 却不带时区标记,`Date.parse` 当本地时间,UTC+8 用户看到的**新鲜**结论显示成 8 小时前,方向还正好反了:让最硬的证据显得最陈旧。改为在 `SourceAuthContract` 的 field validator 统一补齐时区——**放在契约边界而非各 provider**,新 provider 无从绕过,移动 Web 与 CLI 也不必各自再防一次;无法解析的字符串原样透传而非清空(`""` 会被读成"从未验证")。另外顺带收紧结构校验门:必填 cookie 名现要求**存在且非空**(`SESSDATA=` 登录不了任何人),这同时消除了它与 rdt-cli 自身解析器(丢弃空值)的分歧——一份 Reddit cookie 曾可能通过校验后被它刚刚为之校验的 store 拒收。
- **外部复审第二轮：两条「只堵住了举例的那条路径」+ 一次自查连带修复**：Codex 复验上一条的 12 项修复时发现两条**只覆盖了被举例的路径**，病根相同——验证了「这个**平台**成功过 / 这**次调用**要不要校验」，而契约需要的是「这**份凭据**成功过 / 这**条路径**都要校验」。(1) **X 换 cookie 继承旧凭据的 `verified`**——`last_success_at` 只记「成功过」不记「谁成功的」，于是写入一份从未发起过任何请求的新 cookie，直接继承上一份的结论**连时间戳都一字未变**；`clear_relogin_block()` 救不了（健康行本就是 `ok`，无 block 可清，返回 `False`）。这与上一轮修掉的「活体缓存按平台取值」是同一个错误换了个 store，因此用同一种解法：新增 `last_success_credential` 记录产生该成功的凭据指纹，由 producer 在解析 cookie、构造 `XClient` 的同一处绑定（"记录成功的那份凭据"即"发出请求的那份凭据"），读取时与当前 cookie 比对，不符即非证据。**刻意不挂在写入路径钩子上**——cookie 也可能经环境变量或直接改 data file 变更，那些路径一个钩子都不经过（复审给出的复现正是直接写 store）。build 期绑定亦是安全方向：cookie 变了而 producer 未重建时，指纹仍跟着真正在发请求的那份凭据，新 cookie 显示 `unverified` 而非冒领。(2) **废弃路由仍能一键关掉活体校验**——上一轮以"扩展总是发 true"为由保留了 `POST /api/bilibili/cookie` 的 `validate_with_bilibili`，实测传 `false` 会让结构完整但已失效的 cookie 在**探针零调用**下落盘。"扩展总发 true"是兼容性论证而非安全性论证：请求不只来自扩展。字段**仍接受但不再生效**（装机扩展每次同步都发它，拒绝该键会 422 掉它们的 cookie 同步——"接受这个字段"与"这个字段能降低校验"是两件事），`validate_credential()` 的 `live` 参数一并删除,写入面从此没有任何"少查一点"的入参。(3) **自查连带修复**——按同一标准复查上一轮其余 10 条,确认 `probes.record()` 全部带指纹、8 处凭据写入(7 条废弃转发 + 统一端点)全部经 `_credential_landed`、无其他请求侧校验开关;查出两处新问题并修掉:**其一**,第 (2) 条修复本身在 X 上制造了一个全新的 #11 式自相矛盾——`verification` 变诚实后,`_X_STATE_DETAIL['ok']`(「X 来源正常，cookie 有效。」)仍挂在读作「待验证」的 chip 下方,这是本轮唯一一处**有意移动**的 legacy `detail`(冻结用例同步更新;真正确认过的那条路径逐字节保持原文案);**其二**,`_verify_twitter` 把 SQLite 的裸时间戳直接拼进用户可见文案,是上一轮时区修复漏掉的另一扇门(时间戳归一化因此从 field validator 提取为模块级 `normalize_timestamp()` 复用)。另外把 `PUT /api/config` 延迟写入的**部分失败**从裸 500 改为具名错误:四个 store 之间没有事务,I/O 失败必然留下部分已写,但报错须说清哪个平台失败、哪些已经落盘、重试是幂等的(pitfall #7)。
- **「稍后再看 / 我的收藏」列表卡片补齐并优化反馈操作（issue #111，三个图形界面）**：两个 saved 列表的卡片此前只有 同步 / 移除,现补上一行低调的反馈操作——喜欢 / 不感兴趣 / 聊一聊(纯图标幽灵按钮)+ 一个「跨列表」保存 toggle(稍后再看卡=收藏、收藏卡=稍后再看),靠留白分组、封面保持干净。经 ui-ux-pro-max 评审去冗降噪:删掉与「移除」重复的「所属列表 toggle」与桌面的 dismiss,所属列表成员仍由「移除」管理。喜欢 / 不感兴趣 / 聊一聊因 saved 项不带 `recommendation_id`(推荐维度 `/api/feedback` 无之即 404),改走内容维度信号 `/api/events`(与扩展原生 like/dislike 同通道,`type=feedback` + `metadata.feedback_type`,画像消费与推荐反馈一致,仅不触发推荐流的下次刷新隐藏);跨列表 toggle 复用各端 saved 注册表。**桌面 Web / 移动 Web / 插件 popup 三端统一实现并真机 E2E 验证**(桌面新增 `savedCardFeedbackBarHtml` + 复用 `handleSavedCardFeedback`,推荐卡 `cardFeedbackBarHtml` 字节不变;移动 `web/js/saved.js` 自包含 + 新增 api `sendBehaviorEvents`;popup 复用 `bindWatchLaterToggle/bindFavoriteToggle` + 新增 `sendBehaviorEvents`),CLI 无卡片 UI 排除;后端零改动(复用既有 `/api/events`)。新增 `tests/test_desktop_web_saved_card_actions.py`、`tests/test_mobile_web_saved_card_actions.py`、`extension/tests/popup-saved-card-actions.test.ts`。
- **「稍后再看 / 我的收藏」卡片封面/标题/作者空白回填(issue #111 顺带修)**：部分内容(尤其 B 站 / Reddit)被保存进 saved 列表时 `cover_url`(及偶发的标题/作者)没写进 `saved_items`,导致卡片缩略图空白,但 `content_cache` 里其实有封面。`Database.list_saved_memberships` 现按 `content_id`/`bvid` 从 `content_cache` 兜底回填空的 `cover_url` / `title` / `author_name`(相关子查询,只读侧增强、不改存量数据、`NULLIF` 只填空值不覆盖已有);移动端保存 payload `normalizeSavedItemInput` 也补齐 `cover ?? pic ?? thumbnail ?? image_url` 兜底字段(与桌面对齐),防止后续再丢。存量空封面 saved 卡随之显示真实缩略图。新增 `tests/test_saved_list_cover_backfill.py`。

## v0.3.172：候选补货空转冷却——源枯竭不再烧 CPU（2026-07-16）

后端源码走 `backend-v0.3.172`（backend tag 与 Docker 镜像完整可用）；desktop/聚合发布因桌面上传卡死且缺 extension tag 未完整发布，已降级 prerelease，桌面与插件用户请直接升级 v0.3.173。

- **候选源枯竭不再让补货协调器以 2–3 Hz 热轮询（用户报告：更新后笔记本风扇狂转、发热，退出桌面端恢复）**：连续无产出的 supply 结果改按 30/60/120/300/600 秒阶梯冷却，模拟小时从约 9000 次空转降到不超过 10 次；任意 runtime 通知仍可立即穿透当前窗口一次，手动/配置/启动通知与真实产出会复位阶梯，旧式非 mapping 结果保持不节流。空 refresh plan 的全量 INFO 与三组数据库诊断按指纹和 300 秒窗口节流，重复调用保留带累计数的 DEBUG；阶梯首次登顶每个枯竭事件只发一条 `candidate supply starved` WARNING，runtime status 新增 supply streak 与 cooldown deadline 字段。规格与实施计划见 `docs/plans/2026-07-16-supply-spin-cooldown-{spec,plan}.md`。

## v0.3.171 / extension v0.3.171 / desktop v0.3.171：旧版补池兼容、长期避雷与源码更新修复（2026-07-16）

后端源码走 `backend-v0.3.171`，浏览器插件走 `extension-v0.3.171`，桌面安装包走 `desktop-v0.3.171`。

- **撤销的赞不再以满强度留在画像证据里（retraction 确定性折价，事件采集补全 Wave A）**：用户明确「反悔」的正向行为（取消赞 / 取消收藏 / 取消关注 / 取消转发）此前只被记为 neutral 的 retraction 事件，被撤销的那次正向证据仍以 0.85/1.0 满强度继续参与后续画像消费。现在双面折价：**内存面**——`ProfileUpdatePipeline.ingest_batch()` 开头新增原子折价预处理，早于任何阈值消费，把同批 / 缓冲中同 identity key（tweet_id / bvid / mid / xhs note_id，统一到共享 `sources/identity_keys.py`）、事件类型 == `retracted_action`、且事件时间早于 retraction 的正向信号折到 `retracted=true` + `signal_strength≤0.2`；乱序到达用内存 tombstone（`(key,action)→retraction 时间`，TTL 24h / cap 500）处理，`like→retract→like` 的重新点赞不折，事件时间缺失保守不折。**离线重读面**——`Database.mark_positive_events_retracted()`（`/api/events` 钩子 + `openbiliclaw init` 全量重建 / 12h 认知整理）按 identity key 全局标注，迟到正向事件（account_sync 回填旧 like）在落库路径对账已存 retraction 行。偏好与 12h 认知觉察两个重读 LLM 消费面共用 `render_retraction_marked_events()` 给渲染 context 追加「(已撤销)」，偏好 system prompt 增一条静态撤销语义规则；无 retraction 的事件集渲染字节不变（回放不变性有测试兜底）。`retracted_action` 白名单 `{like,favorite,share,follow}` 越界跳过 + WARNING。规格与实施计划见 `docs/plans/2026-07-16-event-capture-completion-{spec,plan}.md`（经 Codex 对抗 review 六轮）。
- **用户亲手写的评论 / 弹幕正文首次进入画像证据链（事件采集补全 Wave B）**：此前除 X 经 GraphQL tap 发出的 comment 事件（正文被刻意丢弃）外，各平台评论只记「点了评论按钮」，B 站弹幕零采集。现在评论 / 弹幕正文**均在提交成功后经网络层采集**：X tap 从 reply `CreateTweet` 提取 `variables.tweet_text`，仅当响应无 GraphQL `errors`（业务码校验）才附带正文（既有 comment 事件发射时序不变）；新增 MAIN-world `src/main/bili-interact-tap.ts` 观察 B 站 `POST …/x/v2/reply/add`（评论）与 `POST …/x/v2/dm/post`（弹幕），仅当响应 `code===0` 才发，桥接 `content/bilibili.ts` 构造 `comment` 事件（评论 `comment_kind="comment"`、弹幕 `comment_kind="danmaku"`+`signal_strength=0.6`，校准注释：低于书面评论 0.75、持平 follow 0.6）。正文经**双端净化**（扩展 `shared/text-sanitize.ts` 与后端 `sources/event_format.py::sanitize_comment_text` 各自截断 200 字符 + 剥离 Unicode category-C，`comment_kind` 白名单 `{"",comment,danmaku}` 越界清空 + WARNING），偏好分析 preserved keys 保留正文进 LLM，渲染 context 追加「评论:『…』」。kernel 新增 `tapAuthoritativeActions` 按动作粒度 DOM 抑制契约（取代旧 `strongSignalSource`）：X 声明 `{like,favorite,share,comment,retraction}`、B 站声明 `{comment}`，消除「网络提交 + DOM 点击」双计与「仅打开评论区即记事件」假动作。隐私政策与商店 listing 同步扩大「个人通讯」采集范围。规格与实施计划见 `docs/plans/2026-07-16-event-capture-completion-{spec,plan}.md`。
- **小红书赞 / 收藏强信号从 DOM 裸奔升级为网络层认定（事件采集补全 Wave C）**：此前 xhs like/favorite 靠按钮文案匹配，图标按钮直接漏采、`aria-pressed` 撤销「名义覆盖实际未验证」。现在新增 MAIN-world `src/main/xhs-action-tap.ts` 观察写端点 `POST …/v1/note/like`→like、`/note/dislike`→retraction(取消赞)、`/note/collect`→favorite、`/note/uncollect`→retraction(取消收藏)，仅当响应业务成功（`success===true` 或 `code===0`，不变量 7b）才发，`postMessage`（`obc-xhs-action`，与 token sniffer 的 `obc-xhs-sniffer` 隔离）桥接 `content/xhs/action-event.ts` 构造 like/favorite/retraction 事件，事件 URL 由 note_id 拼 `…/explore/<note_id>`，与后端 `sources/identity_keys.py` note 键型互通（Wave A 的赞→撤销折价能对上同一批事件）；xhs adapter 声明 `tapAuthoritativeActions:{like,favorite,retraction}`，kernel 抑制这三类 DOM 发射（comment/share 仍走 DOM）。补齐 xhs adapter 单测（note-id 24-hex 边界 / page-type / action 推断 / 图标按钮无文案→null 的降级契约）——xhs 此前是仅有的两个缺 adapter 单测的平台之一。note-card DOM selector 从 passive.ts / bootstrap.ts 双处重复统一到 `content/xhs/selectors.ts`（纯移动零行为变化），被动降级契约（空 title→null、部分字段缺失→部分数据不抛）固化为回归测试。后端 `POST /api/sources/xhs/observed-urls` 的裸链 `urls` 分支从只收 `/explore/` 扩为兼收 `/discovery/item/`（与 `notes` 分支对齐，不再静默丢弃）。xhs 端点形状按公开社区文档构造，待真实端到端验证。规格与实施计划见 `docs/plans/2026-07-16-event-capture-completion-{spec,plan}.md`。
- **事件获取层可靠性修复（2026-07-05 事件获取体检的落地）**：五项联动修复原始行为数据的丢失 / 双计 / 静默失效。(1) 插件事件缓冲从纯内存改为 awaited 写穿持久化到 `chrome.storage.local`——MV3 service worker 空闲 ~30s 即被回收、恰与 30s flush 周期同量级，此前被杀即整批丢事件；强信号在网络 flush 前已落盘，SW 冷启动经 `bufferReady()` init gate 恢复。后端 `not_initialized` 时事件改进停车场（500 条 / 48h TTL）而非消费即弃，初始化完成后自动补发。(2) `AccountSyncService` 新增 48h 跨源去重：扩展已实时上报的同一行为（view/favorite 按 bvid、follow 按 mid、X 按 tweet ID）不再被账号拉取二次写入双倍加权；查询排除自身来源（`exclude_source="account_sync"`），仅历史 API 观察到的重看不受误伤。(3) 账号拉取的画像更新不再绕过洋葱层管线直接整层重算 preference——画像就绪后与实时事件同走 `ProfileUpdatePipeline` 增量路径（四行回退矩阵，管线不可用时 WARN 回退旧路径，cookie-only 安装的首次 bootstrap 不变）。(4) 同步失败不再静默：各阶段异常 WARN 落日志并分类为 `auth_expired` / `error`（新 runtime-status 字段 `last_account_sync_error_kind`，**需重启后端生效**），桌面 Web 在有错时显示状态 chip，B 站 Cookie 失效终于用户可感知。(5) X 获得服务端 6h 定时增量（复用 init 的 `XClient` + `resolve_x_cookie`）：likes/bookmarks 各拉 200 映射为 `like`/`favorite` 事件（`source_platform="twitter"`），tweet ID 集合去重（上限 2000），首轮从 events 表已持久化 X 事件播种——不开浏览器 X 数据也不断更。规格与实施计划见 `docs/plans/2026-07-05-event-acquisition-fixes-{spec,plan}.md`（经 Codex 对抗 review 两轮）。
- **事件采集精度与覆盖改进（2026-07-05 事件获取体检第二批）**：六项修复让原始信号更准、更全。(1) X 取消操作（unlike/unbookmark/unretweet）此前被 GraphQL tap 直接丢弃——现映射为 `feedback` retraction 事件（满意度恒 neutral、`signal_strength=0.2`、不进反馈批学习、不走强信号旁路：撤销是"中和"非负偏好）。(2) DOM 强信号读 `aria-pressed`：点已激活的赞/收藏/关注按钮识别为撤销而非再记一次正向（X 由 tap 权威、DOM 只压制不重复发）。(3) `watch_seconds` 从墙钟改为**真实播放时长**（分段累计，暂停/闲置不计；额外报 `page_dwell_seconds`），并对晚渲染 `<video>` 有界重试挂载。(4) xhs 笔记/知乎回答/reddit 帖子/X status 内容页从零信号变为**进入发 view + 可见性门控的停留时长**（后端对无时长内容页停留按 ≥30s→positive「engaged_reading」分类）。(5) 搜索补漏：除 Enter 外从结果页 URL 提取关键词（覆盖点按钮/点联想词，抖音按路径段、X `/explore` 不发），Enter 与 URL 两路 10s 去重；**搜索信号权重 0.25→0.5**（提到 passive view 之上，行为变化）。(6) account_sync 收藏夹扫描 10→200 夹（500 条预算封顶请求），关注加翻页循环（≤5 页 500 人），覆盖此前第 11 夹/第 2 页起的盲区。规格见 `docs/plans/2026-07-05-event-capture-depth-{spec,plan}.md`（经 Codex 对抗 review 两轮）。
- **“立即应用”不再在 pip/venv 安装上误报“更新后依赖安装失败”**：用户从 0.3.168 应用 0.3.169 时，源码已成功快进，但更新器仅凭仓库存在 `uv.lock` 就无条件执行裸 `uv sync`；官方安装器明确支持的 pip/venv fallback 环境里没有 `uv`，因此稳定抛 `FileNotFoundError('uv')`，又被折叠成无细节的 `dependency_sync_failed`。现在先确认 daemon 的 PATH 里确有 `uv` 才走 `uv sync --no-install-project --inexact`，否则从 `pyproject.toml` 读取运行依赖并用当前后端 Python 的 `pip` 同步；两条路径都不重装正在运行、Windows 可能锁定的 editable console entry。同步命令、退出码与 stderr 写入本地日志，状态 API 的 `last_error` 同时给出工具/退出码/超时等安全摘要；重启统一改走 `python -m openbiliclaw.cli <原参数>`，不再把 Windows 的 `openbiliclaw.exe` 当 Python 脚本执行。新增无 `uv` 选择回归、dependency-only uv 参数、失败诊断和 Windows 模块重启测试；0.3.168→0.3.169 的依赖/lock diff 复核确认只有自身版本号变化，排除发布依赖损坏。
- **Issue #113 混用旧桌面版与新源码时，补池不再被 `content_cache.item_key` 唯一索引击穿**：v0.3.166 及更早的写入器不知道 `item_key`，打开被新版迁移过的共享数据库后会把每条新候选都写成默认空串；旧的全量唯一索引于是只允许第一条入池，后续 B 站 / 抖音候选全部报 `UNIQUE constraint failed`。现在 canonical 非空 identity 继续由 partial unique index（`WHERE item_key != ''`）保护，同时增加普通 lookup index；初始化会临时移除旧全量 guard，补全空 identity、合并与已有 canonical 行的碰撞，再恢复 partial unique。旧版可以继续写入，当前版下次启动会自动修复，新增连续 legacy 写入与 canonical 冲突合并回归。
- **对话里明确说“不想看”后，长期画像与候选池会在回复外真正收敛**：`SocraticDialogue` 的用户主动学习链使用 task-local background-admission bypass，所以即使 canonical 库存为空或后台 LLM 暂停，`dialogue_insight → preference → profile/purge` 也不会被 `maintenance` 门禁永久 park；所有调用仍受 runtime total gate 约束。`SoulEngine.learn_from_dialogue()` 在高置信或重复信号真正新增 `disliked_topics` 后计算新旧差集，偏好一落盘就先调度共享 `purge_pool_for_new_dislikes`，精确匹配立即标成 `purged_by_dislike`，embedding 召回 + LLM 精判与完整画像重建并行，不再被后者的数十秒耗时拖住。对话 system prompt 同时明确“OpenBiliClaw 本地长期画像 / 候选过滤”和“不能修改平台自身算法”的边界，不再错误回复成只能记住当前聊天上下文。回复依旧不等待学习与清池，现有等待钩子供测试和优雅关闭收敛。
- **Discovery eval embedding 预过滤进入 shadow rollout**：`[discovery].eval_prefilter_mode` 新增 `off` / `shadow` / `enforce` 三档，默认 `shadow` 只记录 `prefilter-shadow` would-filter 候选并继续送 LLM；确认 would-filter 中几乎没有高于 admission 门槛的内容后可切 `enforce`，由本地 embedding 相似度提前缓存低分并跳过 LLM。`explore` 候选始终豁免，enforce 单批过滤超过 50% 时自动 fail-open。
- **LLM token diet 收口到评估 / 表达热路径**：LLM caller bucket 补齐 `discovery.keyword*`、`discovery.x*`、`discovery.douyin*`、`runtime.bilibili_extension_search*`、`pool_purge*`、`api.sentiment*`，整条高频链路都能用 `[llm.discovery/evaluation/recommendation/soul]` 分层模型配置覆盖。内容评估画像实验性使用 compact summary（20 核心 / 48 兴趣 / 32 域 × 16 specifics / 12 recent，长期避雷不裁剪），每条候选另带从画像块外长尾（权重 49..256 名）召回的最多 3 个 `related_interests` 兴趣名（画像不超过 48 兴趣时零开销），digest 覆盖 compact 画像和召回池；48 / 16 只有通过新的 fail-closed artifact v2 才能 landing。推荐表达 / legacy 分类共用同一个 `compact_content_prompt_profile_summary()` 收口，`interests=` 内容相关替换保留。原定 discovery / recommendation `body_text` 200+100 截断随后被严格 Reddit 100×3 门否决并完整回滚，所有这些路径继续使用完整正文。抖音 / X / YouTube / 小红书 / B站扩展兜底的关键词生成统一改用查询瘦身画像 `build_query_generation_profile_summary`，与主 search / explore / keyword_planner 相同的稳定口味输入。（坏输出重试沿用 main 的 keyed-sibling 缺失成员有界重试，未采用早期二分拆批方案。）
- **画像 → LLM 序列化统一收口第一波（profile-views Wave A）**：(1) 补上唯一确认的泄漏——页面内容提取器 `sources/llm_extractor.py` 此前继承 `inject_core_memory=True` 默认，把整块 core memory（含 `personality_portrait`）塞进每次提取 prompt 且随画像变动打断缓存前缀，现与所有兄弟内容管线调用一样显式 `inject_core_memory=False`。(2) 新增 `tests/test_profile_views_guards.py` 钉住两条此前无测试守卫的不变量：`personality_portrait` 绝不进入三个内容管线 dict serializer 或 speculator 的 `to_llm_context(include_portrait=False)`，且每个 serializer 对相同画像输入两次调用字节一致（canonical `json.dumps`）；openclaw `ProfileResponse` 的 `[:5]` 截断补上 8 项输入回归。(3) 清理死管道：`soul/dialogue.py` 的 `_respond_with_tools` 曾用 getattr 探测一个任何 service 都不存在的 `_build_core_memory_block`，分支永远走空——移除探测，`build_socratic_dialogue_prompt` 的 `core_memory_text` 保留为文档化的测试注入 seam，CLAUDE.md 例外段落改为指明真正注入点是 `llm/service.py` 的 `complete_with_core_memory`。(4) 新增 `docs/profile-usage.md` 画像使用登记表，逐消费面记录触发频率 / view / 字段 / 上限 / 是否含画像 / 是否走 LLM，每行 file:line 对当前工作区核实。规格与实施计划见 `docs/plans/2026-07-18-profile-views-{spec,plan}.md`。
- **聊天核心记忆尊重手动编辑 + system 前缀稳定化（profile-views Wave C，Task 6）**：两处修复。(1) **overrides 生效**——`MemoryManager.get_core_memory()` 此前直接读原始 soul 层，从不叠加 `profile_overrides.json`，用户手动改画像后聊天视而不见；现新增 `_effective_soul_data()` 在 manager 内同步做 `OnionProfile.from_dict → apply_overrides → to_dict`（与 `SoulEngine.get_profile()` 同源的 AI ⊕ overrides，且不改动任何同步函数签名），overrides 为空时短路返回原始层、行为与开销均不变。(2) **缓存前缀拆分**——新增 `soul/profile_views.py:chat_core_memory` view 与 `MemoryManager.render_core_memory_blocks()`，把核心记忆拆成 `stable_block`（用户画像 + 核心特质/价值观/深层需求/MBTI + 偏好摘要，字节稳定）与 `volatile_block`（近期观察 + 当前洞察，每认知周期都变）；`LLMService.complete_with_core_memory` 全注入族（chat / tools / socratic / structured / multimodal）统一把 stable 注入 system 前缀、volatile 置于 user 消息本轮输入之前（most-stable-first），觉察/洞察刷新不再打碎 provider prompt 缓存的 system 前缀。`render_core_memory_prompt()` 保留为「stable + volatile」拼接兼容包装。新增 (a) override 生效 / (b) 觉察 churn 下 system 块字节不变 / (c) 段落齐平 golden（对拍搬家前快照，零内容丢失）三道验收测试。规格与实施计划见 `docs/plans/2026-07-18-profile-views-{spec,plan}.md`。
- **猜测器画像序列化收口（profile-views Wave C，Task 7）**：兴趣猜测器 `soul/speculator.py` 与避雷猜测器 `soul/avoidance_speculator.py` 此前各自直接调用 `profile.to_llm_context(include_portrait=False)` 这条独立的字符串序列化分支，字段集与 `build_profile_summary` 各自漂移且无守护。现新增 `soul/profile_views.py:speculation` view 收口该入口——采用最保守方案（内部委托 `to_llm_context(include_portrait=False)`，段落/顺序/画像排除全零变化），两处调用点改走 view；避雷侧保留 `getattr` 对非对象 profile 回落 `{}` 的兜底语义，并把 `build_avoidance_generation_prompt` 的 `profile_summary` 形参放宽为 `str | dict[str, object]`，与运行时实际传入字符串对齐（此前是靠 getattr 的 Any 掩盖的类型谎言）。以搬家前 `to_llm_context` 输出固化三画像 golden（`tests/golden/profile_views/speculation__*.txt`）逐字节对拍，守护套件补 sentinel 画像排除 + 两次调用字节一致。规格与实施计划见 `docs/plans/2026-07-18-profile-views-{spec,plan}.md`。
- **画像 → LLM 序列化门面收口（profile-views Wave B）**：三个内容管线序列化器 `build_profile_summary` / `compact_content_prompt_profile_summary` / `build_query_generation_profile_summary` 从 `discovery/strategies/_utils.py` **原样迁入新模块 `soul/profile_views.py`**（机械搬家、零行为变化），连同它们的私有 helper、模块级常量与两个查询生成 view 依赖的叶子工具（`normalize_match_text` / `_coerce_query_embedding_vector`——`soul` 层不得 import `discovery`，故下沉到 soul 再由 `_utils` 回流）。`_utils.py` 对所有旧名字（含 `_CONTENT_PROMPT_*` 常量）保留 re-export，`discovery` / `recommendation` / `runtime` / `sources` 的现有 import 路径全部不断。新增 `tests/test_profile_views.py`：把「年轻 / 成熟 200+ 兴趣 / 旧版扁平」三种画像在搬家**前**固化为 golden 快照，搬家后经新模块渲染逐字节对拍（3 画像 × 3 view = 9 组全等），确保「前后输出一致」是被真实验证而非恒真断言；另加结构守护，断言三序列化器 `def` 仅存在于 `soul/profile_views.py`，且内容管线只从 `profile_views` 或 `_utils` re-export 引用。CLAUDE.md 新增约定：新增携带画像的 prompt 必须复用 `soul/profile_views.py` 的 view，不得自建序列化器。规格与实施计划见 `docs/plans/2026-07-18-profile-views-{spec,plan}.md`。
- **维护类调用点 core-memory 注入逐一审计（profile-views Wave C，Task 8）**：八个此前继承 `inject_core_memory=True` 默认的 Soul 维护 / 画像调用点逐点判定并落地。四个改为显式 `inject_core_memory=False`（经 `without_core_memory_kwargs`）：`soul/consolidator.py` 簇裁决（合并/保留只按 user prompt 的簇成员列表判，画像无关）、`soul/category_migration.py` 分类映射（纯分类名规范化）、`soul/pool_purge.py` 厌恶精判（判定材料已全在 user prompt）、`soul/dialogue_insight_analyzer.py` 洞察抽取（prompt 已显式 `json.dumps(core_memory)` 进 user 消息，注入是逐字重复——关闭后模型仍经显式参数看到完整 core memory）。四个有意保留并在代码处注释理由：`soul/layer_updaters.py` 的 role / values / core 三个层更新（更新画像层自身，注入上下文帮 LLM 把新证据 connect 到用户情境）、`api/app.py` 探针情感判定（聊天邻接，在用户自身语境读语气）。consolidator 安全性用确定性对拍验收（真实 `--dry-run` 在 temperature 0.2 下本身不可复现，故以 mock LLM 钉住裁决 payload 与注入开关无关、op 解析一致——见 `tests/test_profile_consolidator.py`）；各 opt-out 点补 kwargs 断言测试（`tests/test_maintenance_injection_audit.py` + 各模块既有测试）。注入默认表见 `docs/modules/llm.md`，逐点决策表见 `docs/profile-usage.md`。规格与实施计划见 `docs/plans/2026-07-18-profile-views-{spec,plan}.md`。
## v0.3.170 / extension v0.3.170 / desktop v0.3.170：换一换尾延迟与库存维护隔离（2026-07-15）

后端源码走 `backend-v0.3.170`，浏览器插件走 `extension-v0.3.170`，桌面安装包走 `desktop-v0.3.170`。

- **「换一换」偶发 8–12 秒停顿改为隔离、分批的 SQLite 路径**：真实日志证明 MMR worker 已完成后，主协程会被同事件循环里的同步 pool maintenance 卡住；现在 recommendation serve 与后台维护分别使用 database-owned 单线程 worker 和独立短生命周期连接。`PoolServeSnapshot` 在一次读事务中合并 readiness、候选、平台补位、最近已看与 curator 信号，最近浏览身份每个快照只解析一次，并复用按最新 view event id 自动失效的缓存；recommendation + shown 用独立短事务原子提交，API 直接复用 `ServeResult.pool_counts_after` 广播并在响应外通过 serve worker 精确复读。维护每事务最多修改 50 行、每 tick 最多 8 批，批间释放写锁并让出 event loop，75ms 写锁冲突直接延后；readiness 未变化的普通 tick 跳过 ranked 重维护、每 10 分钟安全巡检，suppressed 恢复改用内存 source/topic 计数，移除逐行 window-function 重扫。日志拆出 snapshot / embedding / selector worker / event-loop resume / persist / status publish / maintenance 各阶段，详细候选与维护明细降到 DEBUG；新增并发 heartbeat 回归、固定候选顺序回归和 `scripts/benchmark_reshuffle_latency.py`（health 使用独立预热连接）。80MB 生产数据库与 608MB embedding cache 的隔离副本实测换批 P50 约 0.47–0.50 秒、最大约 0.59 秒；强制重维护并发 health 187 次，P99 40.1ms、最大 94.4ms。

## v0.3.169 / extension v0.3.169 / desktop v0.3.169：初始化全链路收口、模型路由与经典主题（2026-07-15）

后端源码走 `backend-v0.3.169`，浏览器插件走 `extension-v0.3.169`，桌面安装包走 `desktop-v0.3.169`。

- **桌面 Web 新用户默认使用经典配色，老用户平滑保留动态色相（PR #118）**：新增可在「设置 → 前端」切换的经典固定色板与动态主题色；首屏脚本会在 CSS 加载前迁移并持久化 `obc.accentStyle`，已有 `obc.themeHue` 的用户继续使用动态主题，避免重载闪色。首次经典模式引导改为独立、8 秒自动收起且 hover / focus 暂停的提示，不再占用业务 Toast 队列；主题单选支持方向键 / Home / End，设置下拉框回归原生语义，经典模式恢复可见焦点环，粗指针保存按钮保持 44px 触控区域。`classic.css` 同步进入桌面静态资源版本指纹；纯桌面 Web 变更，移动 Web、扩展与 CLI 行为不变。
- **初始化探针不再误判 DeepSeek 或偷跑 `llama3`**：通用 chat health check 固定以 `reasoning_effort=""` 发最小探针，DeepSeek 不再把一次 `hi` 放大为 16K/32K thinking 请求并在 30 秒门禁内假超时；DeepSeek 的每次推理档位改为请求局部参数，不再临时修改共享 provider 状态，并且 `[llm.deepseek].base_url` 现在真正传入 SDK 与 endpoint 代理裁决，第三方中转不再被静默忽略。Ollama chat 只在 `[llm.ollama].model` 明确非空时注册，仅有 `base_url`、只配置 bge-m3 embedding、或只把 Ollama 写成 default/fallback 都不会再隐式构造 `llama3`；配置保存与安装器会直接提示缺少模型，readiness / registry 全量健康检查也跳过非 chat-capable provider。
- **多 Provider reasoning effort 默认收敛为 `medium`，渠道短任务仍为空**：OpenAI 官方 GPT-5/o-series（Chat + Responses）、Claude 4.6+、Gemini 2.5/3、DeepSeek V4 与 OpenRouter 都接入各自原生 effort/thinking schema；新配置默认 `medium`，DeepSeek 按官方规则将其归一为 native `high`。`LLMService` 仍按 caller 把 discovery、recommendation、sources、YouTube 搜索、B 站扩展搜索及轻量 eval 的未指定 effort 解析为 `""`：DeepSeek 真关闭，OpenAI/Claude/Gemini 在不可关闭型号下降到最低安全档，OpenRouter 为避免 mandatory reasoning 型号 400 而省略字段。普通 GPT-4、任意泛 OpenAI-compatible 网关和 Ollama 不猜能力、不发送伪参数；显式 caller effort 始终优先。
- **初始化阶段 1 也有全局截止时间，不再被多个来源串行拖成无限等待**：所有选中来源共享 600 秒墙钟预算，B 站 / X 另有 240 / 180 秒单来源上限，六类扩展 bootstrap collector 接受协作取消事件并按 0.5 秒轮询退出；外层超时设置取消事件后还会给 worker 最多 1 秒有界 drain，避免线程池高负载时 run 已终态、旧 collector 才迟到访问任务队列。单来源超时继续剩余来源，总预算耗尽且仍无画像信号才以 `collection_timeout` 可重试失败。阶段 1 的真实来源完成数、已用时和总剩余时间持续下发，B站 / X / 知乎 / Reddit 事件改为一次 SQLite 事务批量落库，减少逐条 commit 的长尾与半写窗口。
- **初始化 running 锁升级为可恢复 owner lease**：`init_runs` 新增 `progress_sequence/progress_at`，把 30 秒 owner heartbeat 与有效业务进展分开；已知 task 退出但终态写失败时由 done callback 和状态端点立即补写 `interrupted`，无本进程 owner 且 heartbeat 超过 120 秒也自动回收，开始 / 取消端点会先 reconcile，不再让幽灵 `starting/running` 永久挡住重试。运行中 init-status 对 B站、chat、embedding readiness / diagnosis 全部只 peek 缓存，避免 3 秒状态轮询在关键路径插入真实探针。
- **真实首启不再被 account-sync 抢跑**：真实隔离数据库回归发现 daemon 启动后会在用户尚未点击「开始初始化」时先导入 B 站历史并启动画像分析，随后 guided init 又拉同一批信号，既浪费模型额度又重复写事件。共享后台 LLM gate 现在在完整画像尚未提交时一律关闭；阶段 3 提交后仍由 active-run gate 挡到阶段 4 终态，严格初始化成为唯一首版画像 owner。
- **初始化完成后不再被恢复的后台任务“反锁死”**：真实端到端跑完四阶段后发现，Reddit producer 恢复时会在 API event loop 里直接等待 `rdt/opencli` 同步命令，真实一次等待约 15 秒，期间 `/api/ping`、`/api/init-status` 和 WebSocket 全部排队；命令后端状态探测与实际抓取现统一移到 worker thread。完成态 `init-status` 同时改为只读 embedding readiness / diagnosis 缓存，不再排队等待正忙于首池预热的向量 provider；实时 readiness 仍由 `/api/health` 探测，force 重建仍在 POST 临界区严格复验。
- **Setup provider 切换与失败诊断按真实环境收口**：`/setup` 会回填当前 provider 的 model / Base URL / API flavor，并按 provider 独立保留编辑草稿，不再把 OpenAI-compatible 的模型名带进 Ollama 或因漏填已保存 Base URL 卡在第一步；切换时清掉上一家的错误。chat 前置探测上限从 15 秒调到 30 秒覆盖 Ollama 7B 冷启动；终态 run 的持久化原因优先于随后 prereq 失败，明确的阶段 2 动态上限不再退化成「AI 服务不可用」。
- **热重载取消有严格 1.5 秒上限**：`BackgroundTaskRegistry` 与 top-level refresh/account-sync/auto-update 取消改用真正有界的 `asyncio.wait`；吞掉 `CancelledError` 的第三方 coroutine 不再把配置保存或初始化启动永远挂住。未退出任务仍保留所有权并阻止同名 loop 重启，避免旧新 runtime 并发写库；正常协作取消行为不变。
- **三端明确区分“后台在线”和“业务有进展”**：popup、桌面 Web 与 `/setup/` 同时消费 heartbeat / progress 双时钟；确定型批次才显示百分比，画像生成与 discovery 使用 indeterminate 流动条 + 已用 / 最大等待时间，不再用 ETA 曲线伪造 49%。三端新增运行中取消按钮、45/60/15 秒 status/start/cancel 请求 deadline 和可见离线提示，轮询失败保留最后已知进度；`running` 优先于 `initialized`，覆盖画像已落盘但首轮推荐仍在生成的双真窗口。桌面 Web 在这个双真窗口结束时还会做一次**非门控** runtime 补水，避免推荐已经出现而顶部徽标 / 侧栏库存仍残留「后端未初始化」；`init-status` 仍是完成判定的唯一权威源。Chromium E2E 覆盖 setup / desktop 双真状态、无假百分比、取消、重试与终态 runtime 刷新。
- **初始化「卡在分析偏好」时日志能看清且不会被网关限流雪崩**：桌面 desktop.log 捕获的是 stdout（不是 logger 的 openbiliclaw.log），此前阶段 2 的分片完成进度只喂给 coordinator/GUI，desktop.log 里只剩 eta 心跳，一旦某个分片挂住整屏都是「已用 Xs / 预计还需 ~0s」，既看不出进展也定位不到卡点。现在:①`_run_with_progress` 心跳带上实时子进度（`已完成 X/N 批`），分片挂住时该计数冻结在增长的时钟旁,一眼定位；②修掉超过预估后 `预计还需` 永久钉在 `~0s`（读着像「马上好」实则永不完成）的误导——超时改显「已超预估(~180s)，仍在处理」；③阶段 2 分片完成行现在无论 CLI/API 路径都回显到 stdout（进 desktop.log）,并额外在 `openbiliclaw.log` 打每个分片带序号的起止 + 耗时明细(started 无对应 done 的分片即卡死/被超时取消的那个)；④chunk fan-out 对齐 `[llm].concurrency`，不再一次创建 6 个请求让单次 429 把等待队列全部打成 cooldown；递归拆分的恢复路径也改为左右顺序推进，不能在有界顶层之下再次形成 4/8/… 个排队子请求；临时 429/cooldown 最多按 65 秒重试两次，402/余额不足立即失败；⑤分片常规输出上限收敛为 4096 tokens，支持该参数的 provider 关闭 reasoning，避免本地模型或推理型网关为一小段 JSON 无界生成；若兼容网关明确返回“reasoning 用尽 4096 且 `finish_reason=length`、无正文”，仅该分片用 16384 tokens 重试一次；⑥同一波任一 chunk 硬失败会取消并 drain 其余 sibling，`InitCoordinator` 也拒绝任何终态后的迟到 heartbeat/progress/complete，确保释放 init 锁前没有遗留模型请求，失败快照不会从 4/6 被污染成 5/6。合并语义不变。
- **Issue #113 的 49% 卡死改为严格、有界的初始化流水线**：共享 `run_guided_init()` 给阶段 2 偏好分析增加按有效并发波次计算的动态墙钟上限（每波 300 秒 + 固定 300 秒恢复预留；1100 条事件在并发 1 / 2 / 3 下分别为 2100 / 1200 / 900 秒，最小单波预算 600 秒），阶段 3 画像生成保持 360 秒上限；超时会取消底层 provider 调用并分别落成 `analyze_failed` / `profile_failed`。阶段 3 现在必须校验并持久化完整画像后才结束，阶段 4 随后严格使用该画像完成「内容发现 → 个性化评估 → 推荐文案 → canonical 可用性校验」。阶段 4 的完整闭环设 600 秒上限，失败按 discovery 部分成功语义结束，不再让整个初始化无限等待。三个 timeout 都是可注入的可选参数，取消仍沿既有 `CancelledError` 路径收尾。
- **首次初始化空库存准入全面解环**：阶段 2/3 的 `soul.*` 调用在 canonical durable inventory 为空时原本会按 maintenance 停在后台 admission，而建立首池又依赖画像先完成。guided init 现在只在这两个顺序任务的 task-local scope 内绕过后台 admission，总 provider gate 仍生效；阶段 4 不继承 bypass。进一步审计发现 `discovery.explore.queries` 也会形成「探索词等库存、库存等本轮发现」的内部循环，现与空库存时的 `sources.*.extract` 一起按 `refill.supply` 准入；用户主动发起的 `api.config_probe` 改为 interactive，仅受总 gate 约束，使空库存故障态仍可测试和修复模型配置。普通后台任务与 Soul / Analyzer / ProfileBuilder 公开 API 保持不变。
- **严格初始化进度与恢复动作同步到三端**：阶段 2 动态上限与阶段 3 的 360 秒硬失败 `detail` 都明确写出本轮分钟上限、Base URL / 模型名 / 网络 / 代理 / 服务过慢等常见原因，以及「到模型设置测试后重试」的下一步；桌面 Web、`/setup/` 和 popup 都显示阶段 2 真实批次与并发上限、阶段 3「生成并保存完整画像」及阶段 4 的发现 / 推荐文案 / 可用性校验子进度，并把典型耗时调整为 4–20 分钟（历史较多或本地模型可更久）。普通 `init_completed` 已由后端保证至少一条 canonical 推荐可浏览，PC 两端不再在终态后读取 runtime 状态并伪造 95% 二次等待；`partial_success + discovery_timeout/discovery_partial` 则明确说明画像已生成、后台继续补池并允许进入应用。取消后的终态状态复用已有前置探测缓存，不会为渲染取消结果再同步等待一次 30 秒 chat probe；错误文本使用 `aria-live`，硬失败切为 assertive 播报，同时保留重试、模型设置 / 上一步及进入应用入口。
- **失败后的正常空画像跳过不再打印假崩溃 traceback**：初始化失败后恢复 runtime 时，keyword planner 读取不到画像本是正常 no-op；日志现在只保留一行原因，不再用 INFO + `exc_info=True` 展开整段 `SoulProfileNotInitializedError`，避免用户把后台正常等待重试误认为又一次锁死。
- **自动更新「未配置 origin 远端」误报改为按真实原因分诊（修复状态卡自相矛盾）**：`origin` 其实存在——只是缺 `url` 行、配了多个 url、或 Windows 上因 dubious ownership 被 git 拒读——时，旧守卫把 `git config --get remote.origin.url` 的任何非 0 返回一律判成「未配置 origin 远端」并让用户跑 `git remote add origin`；照做却撞「error: remote origin already exists」，正是用户反馈截图里的矛盾（卡片说没配、`remote add` 说已存在）。现在读空后补探一次 `git remote get-url origin` 分四类，各给能落地的修复命令：dubious ownership → `git config --global --add safe.directory <root>`（须以运行后端的账户执行，用户自己的 shell 能读库正是因此）；确无 origin → `remote add`；url 为空 / 不可解析 → `remote set-url`；其他 git 错误 → 原样透出首行真实 stderr 让用户按后端日志排查。新增 reason 码 `origin_remote_unusable`（与「远端可读但不在允许列表 / 内嵌凭证」的 `untrusted_remote` 区分开），桌面 Web reason 文案与状态卡「最近错误」的一键修复命令同步；三类分诊各有回归测试。
- **惊喜卡片在抽屉/窄栏宽度不再截断标题与按钮（issue #115）**：`75c0d7f3` 只把单列断点放在 700px，主内容区被 312px 侧边抽屉压到约 700–950px 时惊喜卡仍是「缩略图 + 正文」两列、正文列塞不下标题与整条操作行，标题、`1/1` 队列徽标、`聊一聊` 被右侧裁掉；再往下又直接跳成整屏大缩略图的窄屏样式，中间宽度两头不讨好。改为把 `.delight-actions` 与状态行提到 `.delight` 网格并用 `grid-template-areas` 编排：宽屏保持操作行贴正文列下方（原样）；容器 ≤940px 时缩略图 + 正文仍并排、操作行下沉为跨整卡宽度独立一行（`去看看`/`聊一聊` 钉最右、反馈图标最左）；≤560px 整卡竖直堆叠，≤430px 保留窄屏内联输入框。断点由 `.layout` 的 `@container desktop-main` 按抽屉实际占宽裁决。
- **桌面 Web 停在底部时滚动自动加载不再卡死（issue #115）**：滚到底触发一次自动加载后，若用户立刻又滚到新底部但落在 8 秒冷却窗口内，`IntersectionObserver` 因只在相交状态变化时回调、且此时没有新的滚动事件，冷却结束后再没有任何东西重新发起检查——用户明明看得到「加载更多」按钮、候选池也有货，却要手动上下滚或点按钮才继续。现在把判定逻辑收敛到 `autoLoadBlockReason`（冷却判定移到最后，保证 `cooldown` 意味着其余前置都已满足），当唯一拦路项是冷却且哨兵仍在视口时用 `armAutoLoadCooldownRecheck` 安排一次冷却到点后的一次性复查，自愈续加载；页面被新内容撑高、哨兵移出视口后自然停下，不会连轴补货。移动 Web 走手势 arm 模型、无时间冷却，不受此问题影响。
- **X 源「被限流」状态文案不再让人误以为要重登**：设置页 X discovery 卡片在 `rate_limited`（HTTP 429）时,原文案「被限流,正在退避冷却中」与 For-You 熔断提示「已自动暂停」拼在一起,既没说明 cookie 其实正常、无需操作,又让「会自动重试」和「已暂停」读着自相矛盾,导致用户误判成需要重新登录/更换 cookie。后端 `/api/sources/status` 的 detail 改为「cookie 正常,只是当前被 X 限流。已进入退避冷却,到点自动重试,无需手动操作」,For-You 追加句澄清为「其中 For-You 子流因连续失败已临时熔断,下次抓取成功会自动恢复」;桌面 Web 与扩展 popup 同步把 `rate_limited` 的色调从「需要登录」同款黄色告警降为中性 pending（桌面蓝 / popup 灰），与需用户动手的 `missing_cookie` / `expired_cookie` 视觉拉开。纯文案 + 色调调整,退避/熔断状态机与 429 判定逻辑不变;mobile web 与 CLI 无此源状态卡,不涉及。

## v0.3.168 / extension v0.3.168 / desktop v0.3.168：初始化有界化、多模态封面与升级可靠性（2026-07-14）

后端源码走 `backend-v0.3.168`，浏览器插件走 `extension-v0.3.168`，桌面安装包走 `desktop-v0.3.168`。

- **Issue #113 的 49% 卡死改为有界失败 / 降级完成**：共享 `run_guided_init()` 给阶段 2 偏好分析和阶段 3 画像生成各加 360 秒墙钟上限，超时会取消底层 provider 调用并分别落成 `analyze_failed` / `profile_failed`，把「检查模型地址、网络和代理后重试」直接显示在初始化页；阶段 4 首池补货设 600 秒上限，超时按既有 discovery 部分成功语义结束，不再让整个初始化无限等待。三个 timeout 都是可注入的可选参数，CLI 与 API 默认共用同一上限，取消仍沿既有 `CancelledError` 路径收尾并清理并行 sibling task。
- **后台画像错误真正进入初始化状态接口**：`AccountSyncService` 的 `analyze_events()` 同样有 360 秒墙钟上限，超时会取消任务、写入安全可操作的 `last_sync_error`，且继续保持「不推进游标 / 不写同步时间 / 下轮可重试」语义。`GET /api/init-status` 现在会读取 `last_account_sync_error` 中的画像分析失败：当前 LLM 探针仍失败时保留 `reason=llm_not_ready` 并用真实错误补充 `detail`；探针已恢复时返回 `reason=analyze_failed`、允许用户立即重试。此前 v0.3.166 虽记录了该字段但状态接口并未消费，文档与实现现已对齐。
- **超时原因与恢复动作同步到三端**：360 秒硬失败的 `detail` 现在明确写出「AI 服务在 6 分钟内未返回」、Base URL / 模型名 / 网络 / 代理 / 服务过慢等常见原因，以及「到模型设置测试后重试」的下一步；桌面 Web、`/setup/` 和 popup 对 `analyze_failed` / `profile_failed` 及后台 account-sync 的 `llm_not_ready + 画像分析失败 detail` 都优先展示这段人类文案，不再出现 `analyze_failed` 机器码或通用「条件未满足」。600 秒 discovery 超时会以 `reason=discovery_timeout`、`partial_success=true` 和可见 `detail` 持久化并下发，三端说明画像已生成、首池本次未完成、后台会继续补池；`init_completed` 流事件也携带同一原因。错误文本使用 `aria-live`，硬失败切为 assertive 播报，同时保留重试、模型设置 / 上一步及「先进入应用」恢复入口。
- **不存在的 CA 环境路径不再让所有网络客户端构造即崩溃**：进入 `[network].mode=system` 时会检查 `SSL_CERT_FILE` / `SSL_CERT_DIR` / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`；仅删除指向不存在文件或目录的失效覆盖，让 httpx / OpenSSL 回退到默认可信 CA store，同时完整保留 `HTTPS_PROXY` 等代理环境。存在的私有 CA 路径保持不动，TLS 验证从未关闭。
- **一句话安装不再因脏 lockfile 把用户永久困在旧版**：`install.sh` / `install.ps1` 复跑时，若已有 checkout 落后 origin 但工作树「脏」，此前会静默跳过自动更新——而装机时 `uv sync` / `npm ci` 必然改写 `uv.lock` / `extension/package-lock.json`，于是大量用户在完全没察觉的情况下一直跑几周前的旧代码（有人卡在 5 月 23 号的 v0.3.89）。现在:①若脏文件**只是这些可再生的 lockfile**，自动 `git checkout HEAD` 还原后正常 fast-forward 更新（缺文件的老 checkout 用 `git cat-file -e` 判定，不会中断）;②若存在**真实代码改动**,打印醒目多行告警——当前版本 → 最新版本、「继续将运行旧代码」、改动文件清单、手动更新命令,并支持 `FORCE_UPDATE=1` 让安装器自动 `git stash` 后更新再 `git stash pop` 还原改动;③干净工作树行为不变（正常 FF）。macOS/Linux 与原生 Windows 两条安装脚本同步，真实 origin/落后 checkout 覆盖 lockfile-only / 缺文件 / 真实改动 / FORCE_UPDATE / 干净 五种场景验证通过。
- **多模态封面 embedding 与 DashScope（阿里百炼）provider（重做 #100）**：新增原生 `DashScopeEmbeddingProvider`，支持 `qwen3-vl-embedding` 文本/图片同空间向量；可选 `[llm.embedding].multimodal_enabled` 会预热推荐池封面，并在惊喜推荐和普通推荐中加入有界、只加不减的视觉相关性加成。热路径只读缓存、不现抓封面；缓存覆盖率不足 60% 时整批不加成，避免新内容因先预热而压过旧内容。provider 与开关已接入桌面 Web、扩展设置页、配置 API 和 CLI 展示，默认关闭时排序行为不变。

## v0.3.167 / extension v0.3.167 / desktop v0.3.167：国内大模型网关代理豁免（2026-07-14）

后端源码走 `backend-v0.3.167`，浏览器插件走 `extension-v0.3.167`，桌面安装包走 `desktop-v0.3.167`。

- **国内大模型网关始终直连，不被海外代理误伤**：为访问墙外模型把 `[network].mode` 切到 `system / custom` 时，DeepSeek / 商汤 SenseNova / 通义千问 / 智谱 / 文心千帆 / 混元 / 火山方舟 / Kimi / MiniMax / 阶跃 / 百川 / 硅基流动 / 无问芯穹 / PPIO 等国内网关，以及 localhost / 内网自建端点（cpa、vLLM 等），会被识别为国内 endpoint 并强制直连——不再把国内请求塞进梯子绕道境外导致「商汤请求总是超时」。识别逻辑覆盖 `.cn` 顶级域、已知国内厂商的非 `.cn` 域名白名单，以及 loopback / 私有 / link-local IP；`registry` 的 `_outbound_proxy(base_url)` / `_outbound_trust_env(base_url)` 改为按 endpoint 粒度裁决，chat + embedding 全部 provider 构造点传入各自 endpoint（DeepSeek 固定 `api.deepseek.com`）。豁免按 endpoint 生效，genuine 墙外网关仍照常走全局代理策略。配置页 LLM 测试同样受益（不再需要为国内模型手动切回 `direct`）。该策略由 `openbiliclaw.network.is_domestic_endpoint` 统一裁决，`tests/test_network_proxy_isolation.py` 新增参数化 + 直连/走代理对照断言钉住行为。
- **扩展包版本对齐**：extension 随后端升到 0.3.167 重新签名发布，插件市场同步；本次未改扩展逻辑。

## v0.3.166 / extension v0.3.166 / desktop v0.3.166：海外网络路由加固与初始化错误可诊断（2026-07-14）

后端源码走 `backend-v0.3.166`，浏览器插件走 `extension-v0.3.166`，桌面安装包走 `desktop-v0.3.166`。

- **海外网络路由不再被失效系统代理拖死**：`[network]` 新增 `direct / system / custom` 三模式，默认 `direct` 显式忽略环境 / OS 代理，旧的非空 `proxy` 自动迁移为 `custom`；OpenAI / Claude / Gemini 系 SDK、GitHub 更新、Codex OAuth 与 YouTube 的 yt-dlp / scrapetube / InnerTube / HTML fallback 统一执行同一策略。桌面 Web、扩展设置页和连通性探测同步支持模式选择，Docker 检出或继承代理变量时自动选择 `system`（显式用户选择优先）。
- **初始化卡在「分析偏好」时报出真实原因（issue #113）**：`describe_llm_failure` / `classify_llm_failure_kind` 新增 SSL 证书校验失败与通用连接失败识别（httpx `ConnectError:[SSL...]`、OpenAI SDK `APIConnectionError:"Connection error."` 均不是 Python `ConnectionError` 子类，此前被漏判成泛化错误）；SSL 失败会给出「本地代理/杀软对 HTTPS 中间人拦截或自签证书，请关闭代理或加直连白名单」的可操作提示，优先级高于「所有 provider 失败」。guided-init Stage 2（`analyze_events`）与 Stage 3 一致包装 LLM 异常为 `GuidedInitError("analyze_failed")`，把原因透传到初始化页，不再静默重试。
- **桌面版后台自动建画像失败同样可诊断**：`AccountSyncService.sync_now()` 里的 `analyze_events()` 此前是裸 `await`，对话模型不可用（本地模型未拉取 → 404、网关鉴权 → 401、超时）时异常直接冒泡，用户可见的 `last_sync_error` 从不记录，桌面初始化只表现为无限等待——补上 guided-init CLI 路径之外的这条后台链路：失败写入 `画像分析失败：<原因>` 到 `last_sync_error`（供 `/api/init-status` 与账号同步状态读取），且刻意不推进任何游标、不打 `last_account_sync_at` 时间戳，保证整个 tick 回滚、下次 tick 重试、不被 `sync_interval_hours` 节流锁死，也不消耗一次性的 auto-bootstrap 机会；`CancelledError` 不被吞（热重载/重启打断语义不变）。真实 ollama 404 端到端验证通过。
- **「模型未找到」不再被误判为泛化错误**：`classify_llm_failure_kind` / `classify_llm_unavailability` 新增 `model_not_found` 类别，识别本地 Ollama 模型未拉取（HTTP 404 `not_found_error` / `try pulling it first`）与 OpenAI 兼容端「model does not exist」——区别于 `no_provider`（完全没配 provider）与 `auth_failed`（401）。后台 `account_sync` / 反馈批处理循环因此对该错误只记一行可操作日志（提示拉取模型或改模型名）而非整段 traceback；`describe_llm_failure` 给初始化页返回「先 `ollama pull <模型名>` 或核对模型名」的中文提示。
- **空换批不再抹掉正在看的推荐**：移动 Web 与扩展 side panel 的“换一批”改为事务式替换，只有拿到非空新批次才覆盖当前卡片；后端因过滤、并发消费或库存状态短暂不同步返回 `items=[]` 时保留原列表、停止本轮自动续页并给出明确提示，同时重新读取 `/api/runtime-status` 收敛库存。桌面 Web 原有空响应保护保持不变；CLI 为无持久卡片状态的单次输出，不适用列表保留语义。
- **真实换批不再把后端拖成“未连接”**：在 9.2 万事件、4.1 千推荐历史、300 条可换库存的真实库上复现到 `POST /api/recommendations/reshuffle` 已选出 10 条后超时，期间连 `/api/ping` 都被 SQLite 池维护压住。数据库初始化现在为 `recommendations(bvid)` 与 `events(event_type, id DESC)` 补热路径索引，消除 pool readiness / maintenance 中反复 `NOT EXISTS` 与最近已看查询的历史全表扫描；async refresh / force-refresh / post-refresh 的原子库存维护，以及推荐 serve 的 readiness / 候选读取与过滤、curator 评分和推荐历史批写，统一在 worker thread 执行。`/api/runtime-status` 与消费后的 pool snapshot 也不再同步占用 FastAPI 事件循环，即使成熟库查询或维护较慢，`/api/ping` / runtime stream 仍可响应。启动前维护与 detached shown 小批量提交保持同步，后者避免共享 SQLite 连接在异步任务清理时发生跨线程关闭竞态；原子事务、库存口径与 CLI 行为不变。
## v0.3.165 / extension v0.3.165 / desktop v0.3.165：Firefox 签名安装修复（2026-07-14）

后端源码走 `backend-v0.3.165`，浏览器插件走 `extension-v0.3.165`，桌面安装包走 `desktop-v0.3.165`。

- **Firefox 正式 XPI 恢复发布**：Firefox manifest 的稳定 Gecko ID 改为 `openbiliclaw-firefox@whiteguo233.github.io`，避开旧 ID 已被其他 AMO 作者占用导致的 403；扩展测试锁定该 ID，发布链路通过当前 AMO 账号做 unlisted 签名并要求产出 `openbiliclaw-extension-v0.3.165-firefox.xpi`，签名失败会直接阻止 release。
- **扩展连接徽标不再反复横跳**：popup 将 `/api/ping` 可达性与 runtime WebSocket 状态拆成「已连接 / 重连中 / 未连接」三态；断流后先复检 HTTP，后端仍通时保持功能可用并显示「重连中」，只有探活失败才进入离线轮询。revision guard 会丢弃连接恢复后才返回的旧失败结果，主动切换后端地址关闭旧流也不再误报断线。

## v0.3.164 / extension v0.3.164 / desktop v0.3.164：持续补货、Web 可靠交互与安全对话（2026-07-13）

后端源码走 `backend-v0.3.164`，浏览器插件走 `extension-v0.3.164`，桌面安装包走 `desktop-v0.3.164`。

- **Linux CI 并发回归去抖**：refill 优先级测试先确认新 refill waiter 已实际入队，并持有其槽位到优先顺序断言完成，避免把 refill 正常退出后的 maintenance 准入误判为门控失效；50 轮持续补货 E2E 继续由每轮 2 秒功能超时守卫，移除会受共享 runner 负载影响的额外 1 秒墙钟断言。
- **OpenAI-compatible 结构化 JSON 合约兼容**：`LLMService` 的普通 / 多模态 structured 路径会把已有大写 `JSON` 归一为小写 `json`，若完全缺失则只追加最小 `json` 标记，满足部分兼容端点在 `response_format=json_object` 下的字面消息检查；非结构化调用、业务提示、画像、准入阈值、user 内容和 core-memory 排序均不变。
- **候选批量评估不再虚占 16384 输出 token 配额**：文本与多模态 evaluator 的单次 `max_tokens` 统一收敛为 4096，仍覆盖生产观测中 30 条 JSON 评分约 1500–3000 tokens 的输出，同时避免 OpenAI-compatible 服务按声明上限预留额度并对 8 条真实评估直接返回 `insufficient_quota`。真实商汤回归覆盖空池补货、全部消费后再次评估 / 入池 / 文案回填，以及后台 3 槽占用时交互第 4 槽仍可进入。
- **结构化批处理失败不再放大成请求风暴**：推荐文案与候选评估把 provider 异常和成功响应的 payload 缺项分开处理；429、timeout、connection、5xx 每轮只调用一次 provider，鉴权/缺 provider 暂停等待配置唤醒。malformed 成功响应先持久化有效 keyed sibling，仅对缺失成员做 depth=3、最多六次额外请求的有界重试；仍缺失的文案保持 pending，评估候选按 claim token 无 attempt 增量地回到 `pending_eval`。协调器 transient 退避统一为 15/30/60/120/300 秒并尊重更长 `Retry-After`。
- **推荐文案改为 8/3/30×2 持续微批**：每个 API daemon runtime generation 只拥有一个 expression copy coordinator；待文案达到 8 条立即执行，1–7 条固定从首次通知起最多等 3 秒，通知不延长窗口。OpenClaw one-shot 不启动该 coordinator，而由 inline admission callback 同步完成 bounded copy。API 单轮最多 drain 60 条、provider 请求每批最多 30 条且最多并发 2；OpenClaw 则固定 `limit=4, max_extra_requests=0`，先持久化首 batch 的有效 subset、将剩余行留给下一请求，避免在 45 秒交互窗口内递归拆分。零进展退避 15 秒，60 秒仅作 API safety wake。候选 admission 与分类只发非阻塞通知，热重载会停止旧 generation 后再启动新 owner。参数来自 2026-07-12 生产日志校准。
- **候选评估改用 durable projected inventory**：调度只统计 canonical available、已 admission 待文案与已评估待 admission，普通 raw pending/evaluating 不再虚报库存。3×30 worker 乱序完成时，串行 commit 先保存全部 token-owned 评分，再按 copy-aware headroom 入池；超额达标结果留在 `evaluated`，worker 完成直接补位，60 秒仅作 safety wake。API 与 OpenClaw 使用同一精确 snapshot mapping 和 available gate 值。
- **统一 runtime producer 的候选 claim 所有权**：API daemon 的 B 站 refresh、抖音、YouTube、知乎、X 与 Reddit 都经共享 pipeline enqueue；单次 enqueue callback 立即唤醒 `CandidateEvalCoordinator`，refresh / managed producer 不再同步 `drain_pending()` 另领 batch，避免与 3×30 worker pool 重叠而突破 90 条 durable raw 在途上限。X 不再直接写数据库，因此不必等 60 秒 safety wake，也不会重复通知。OpenClaw direct adapter 不启动 daemon coordinator，明确保留有 90 条硬上限的 inline drain，让一次性 producer 调用在本轮实际 evaluation / admission；独立/CLI 也保留兼容路径。
- **OpenClaw 一次性补货完成文案阶段**：direct bootstrap 不再挂接永远不会启动的 `ExpressionCopyCoordinator`。共享 pipeline 在 inline admission 的 DB commit 后由 OpenClaw 同一请求 await `drain_pending_expression_copy(profile, limit=4, max_extra_requests=0)`；首 batch 的有效文案立即进入 canonical pool，未完成行保持 durable pending 给下一请求，且不会遗留 copy/provider 后台 task。若首 batch 全部无效，本次可能仍无可 serve 行，诊断会保留该事实而不会伪造成功；fresh history 为空时 `recommend --refresh-if-needed` 会从已复制 pool 生成推荐。API daemon 原有 coordinator（60 条 drain、默认 split retry）路径不变。
- **OpenClaw 交互式首批收敛为 4 条**：`recommend(refresh_if_needed=True)` 的 direct bootstrap 在没有 daemon coordinator 时把本轮 source supply、inline evaluation 与同步 copy 都限制为 4，pipeline 同时关闭 4× fetch oversample、固定单个 inline evaluator，并禁止同一请求的 copy split retry；优先在 adapter 的 45 秒交互边界内持久化可用 subset，后续 OpenClaw 请求再补剩余 durable pending。这个内部 bootstrap 策略没有新增 `config.toml` 字段，不改变 API daemon 的 4× supply oversample、coordinator-owned 30 条 worker 波次或共享 LLM gate。
- **后台补货获得两槽新准入保证**：共享 total=4/background=3 gate 新增 cancellation-safe refill admission，优先级为文案 > 候选评估 > 缺货供给 > maintenance；低库存且 refill 排队时 maintenance 新准入最多一个、refill 可借满三槽，库存为零时 park 新 maintenance。规则只影响新 admission，不取消已进入 provider 的 Soul/维护请求；无 refill 可运行时 maintenance 继续借用全部空槽。
- **补货优先级只跟随 durable canonical 库存**：API、OpenClaw 在任何 provider 工作前用数据库可换数初始化 gate，controller readiness、原子维护、推荐消费/文案完成和 candidate snapshot 持续同步 `healthy/refill/empty`。runtime status 新增 refill/maintenance active、waiting、priority-active 与 inventory state，便于定位库存不足时的真实占槽。
- **库存 gate 生命周期补强**：显式注入 API 会从真实 controller target + canonical DB count 初始化 gate，不再因 `ctx.config=None` 把 target 当零；推荐消费只在 detached `mark_pool_items_shown()` 成功提交后通过 callback 更新 canonical state，失败写不误报、callback 失败不拖垮响应；热重载也延迟到所有新组件构造并 atomic swap 成功后才提交 proposed target/state，晚期失败保持旧 gate 原样。
- **热重载后的迟到 shown commit 不再持有旧 controller**：post-commit callback 改由稳定 `RuntimeContext` 终身拥有，所有新旧 `RecommendationEngine` 注入同一个对象；调用时动态解析当前 controller/target，并通过 context subscriber 保留 API `refresh.pool_updated` 发布。连续多次 reload 后，旧 engine 的迟到 detached task 不能把 gate 改回旧 target，新 engine 的 durable commit 仍会发布当前 canonical count。
- **LLM 并发成为 runtime 真正总上限**：API、OpenClaw 与单次 CLI composition 各自只创建一个共享 gate，主服务、Soul、对话与发现链按对象身份复用；默认总并发 4、后台派生为 3。所有 provider 路径都受 total gate 约束，旧 bypass 只能跳过后台 admission；状态接口新增总/后台 active 与 waiting。显式正数配置保持不变，候选评估配置仍默认 3。
- **热重载与配置探测不再逃逸总 gate**：API runtime 在配置重建时原地调整同一个 gate，旧 HTTP/后台学习调用与新服务继续共享总上限；降容等待 active 自然回落，升容立即唤醒队列。`/api/config/probe-service` 的直接 provider 探测按 maintenance 流量进入同一 total/background gate。
- **API 显式注入路径统一 gate 身份**：测试/嵌入式调用传入 Soul 与 runtime controller 时，后端采用双方已有的同一 gate，单侧提供则补齐另一侧，双方不同则启动时立即报错；只有无 gate 的兼容 double 才创建配置容量的新对象。对话与配置探测随后都复用被采用的 gate，且采用过程不会静默调整外部对象容量。
- **显式 Dialogue 与嵌套引擎也纳入身份核对**：`create_app(dialogue=...)` 会采用/注入真实 `SocraticDialogue` service 的 gate，并与 Soul/controller/recommendation 及 controller/account-sync 内可见的 LLM service 一并校验；任一冲突在 mutation 前报错，无属性的兼容 double 保持可用。
- **嵌套 Discovery 使用真实私有 service 属性**：controller 注入审计改为读取 `ContentDiscoveryEngine._llm_service`，不再猜测不存在的 `llm_service`；冲突 gate 会在 mutation 前拒绝，gate-less service 会获得 adopted gate。结构测试同步锁定 Soul/dialogue/recommendation/discovery/account-sync 的真实属性名。
- **升级后先恢复历史合格库存再调用 LLM**：每个 controller 的幂等 startup-maintenance hook 都先在原子 pool maintenance 事务中检查历史 `suppressed` 行；API 启动/热重载由 `run_forever()` 调用，OpenClaw direct bootstrap 则在 adapter service 暴露前同步调用，随后进入 loop 也不会重复维护。只有 `rolled_back=False` 的真实维护结果才完成启动标记；snapshot/DB fallback 或 rollback 保持可重试。仅恢复未推荐、未看过、非 dislike/shown/purged、仍达 admission 与完整 readiness guards 的结果。
- **推荐库存维护改为原子且保护可用底线**：runtime 的 topic/source/stale/explore/raw 维护收敛为一次短连接 `BEGIN IMMEDIATE`；canonical available 按 serve SQL/排序保护，维护后必须满足 `available_after >= min(available_before, target)`，否则整笔回滚。raw ceiling 统一覆盖 `content_cache` 与 active `discovery_candidates`，未领取候选进入可审计的 `trimmed_capacity` 而不是删除，`evaluating` / token-owned 行永不裁剪；source/topic 配额在库存不足时允许延期并输出统一观测汇总。若 BEGIN / canonical snapshot 取得前锁失败，storage 抛出专用异常，runtime 重新读取 canonical available 决策，不再把未初始化计数误报为零库存。
- **知乎来源配额归类修复**：新增七平台唯一可枚举来源族规则表，pool available/raw accounting、discovery 已看过滤、已看事件身份和 URL host 推断统一复用同一别名 / strategy 前缀口径；`zhihu-search/hot/feed/creator/related` 即使旧数据缺少 `source_platform` 也会计入 `zhihu`，不再以五个碎片来源绕过知乎配额。
- **对话失败保留类型且不误学习（issue #107）**：LLM 失败/超时会回滚本轮临时历史，Web durable turn 持久化安全错因与空 reply，CLI / OpenClaw / 三类 Web 客户端显示同一分类且不泄漏上游文本。
- **并发对话历史事务串行化（issue #107）**：同一个 `SocraticDialogue` 实例现在用独立异步锁串行执行普通与工具调用的完整 turn；失败或取消只回滚自己的临时 user turn，等待锁时取消零写入，避免多个 API 入口重叠响应时删掉别轮历史或留下不配对 turn。
- **本机 Ollama 默认端点改用 `127.0.0.1` 并给出超时根因提示**：chat / embedding provider、CLI `setup-embedding` / 模型探测、`ollama_supervisor` 托管端点、`config.example.toml` 与文档示例的默认 `base_url` 从 `localhost:11434` 统一切到 `127.0.0.1:11434`，与 Ollama 默认只监听 IPv4 的行为对齐，避免 `localhost` 被解析到 IPv6 (`::1`) 时连接超时。`ollama_diagnostics` 遇到 `ConnectTimeout`（区别于连接被拒）时额外提示两条真正根因——系统级 TUN 代理（Clash/V2Ray 增强模式）在网卡层劫持了 `127.0.0.1`（`trust_env=False` 拦不住，需加直连白名单），或 `base_url` 仍用 `localhost` 触发 IPv6 解析；该提示会透传进「自动修复已达到上限」文案，让单独安装 Ollama 的用户不再被误导为「服务没启动」。
- **新增 `[network].proxy` 海外出口代理**：一个字段即可让所有海外请求走代理——OpenAI / Claude / Gemini / DeepSeek / OpenRouter / openai_compatible 的 chat + embedding SDK、YouTube（yt-dlp）、GitHub 自动更新、Codex OAuth 令牌刷新。支持 `http` / `https` / `socks5` / `socks5h`，零新依赖（复用已有 `httpx[socks]`）。留空时行为与当前一致（沿用进程 env，Docker 代理探测不受影响）。
- **国内直连严格隔离**：B站 / 抖音 / Ollama / 国内 CDN 图片缓存等 `trust_env=False` 客户端永不使用该代理（继承代理曾触发 B站 风控，`df626f3f`），并由 `tests/test_network_proxy_isolation.py` 守卫测试钉死「未来不得接入 CN 客户端」。
- **保存时拒绝非法值 + 桌面 UI + 连通性探测**：协议 / 主机白名单校验，非法值经 `PUT /api/config` 返回 400 且不落盘，`config.toml` 手改非法值加载时 WARNING 并按空值处理；桌面 Web「设置-通用」与扩展 popup「后端设置-通用」新增输入框和「测试代理」按钮（`probe-service kind=network_proxy`，经待测代理请求 204 端点并区分 `proxy_unreachable` / `proxy_rejected` / `timeout`）。GET 响应对代理 URL 中的账号密码做遮蔽。四端契约：桌面 Web + 扩展 popup 提供设置；CLI 用 `config-show`；移动 Web 无设置页。
- **桌面 Web 滚动自动加载预载边距 300px → 50px**：`#loadMoreSentinel` 哨兵紧贴推荐网格底部，旧的 300px `rootMargin` 约等于一整行卡片高度（16:9 封面 + 文案约 250–350px），导致最后一行（最多 4 张）还在视口下方约 300px 时就追加了新卡片，用户永远看不全当前批次、也到不了「已看完」的干净状态。收到 50px 后，哨兵需几乎滚到视口底部才触发。真实 chromium 浏览器端到端验证（`tests/test_desktop_web_autoload_margin_e2e.py`）：300px 时哨兵在视口下方 150px 即触发，50px 时约 20px 才触发，改回 300 该测试失败——守卫回归而非 grep 源码文本。
- **桌面 Web 首屏渐进渲染与封面请求限流（issue #101）**：首屏改用 `/api/ping` 判断连接，推荐与 runtime 独立于慢 health / 次级读取即时渲染并保留消费后库存复读与 1/2/4/8 秒恢复；推荐网格仅前 4 张封面使用 `eager/high`，其余改为 `lazy/low`，Delight 优先级不变。
- **桌面 Web 侧栏动画与交互细节打磨（PR #102）**：侧边抽屉从 `position:fixed` + `body.side-drawer-open` 推挤改为 flex 行内项，用 `margin-left + transform` 双可插值属性做过渡，展开 / 折叠全程平滑无跳变，并置为 `position:sticky` 固定视口高度让导航常驻；delight 卡片拖拽新增 10px 死区（`_DELIGHT_DRAG_DEAD_ZONE`），微小位移不再误触切换，死区内松手视为点击；单条静态 `#toast` 升级为右下角栈式 `#toastContainer` 通知（进出场滑动、hover 暂停、点击关闭、磨砂玻璃样式）；delight-nav / 反馈按钮加磨砂底衬保证在封面背景上可读；进入聊天页补 `renderChat()` 确保滚到底部。纯桌面 Web 前端改动，移动 Web 与其它三端不受影响。
- **桌面 Web 侧栏、拖拽与自动加载阈值回归（issues #102 / #105）**：真实 Chromium 现锁定侧栏按钮 / 面板 ARIA 同步和 flex 主栏让渡，并逐一验证 Delight 9px 不进入拖动态、10px 进入拖动态、49px 不切卡、50px 切卡；滚动自动加载的 50px root margin 继续由独立 E2E 守卫，未改回过早预载。
- **Delight 按可用主栏宽度响应（issue #106）**：`.layout` 新增命名 inline-size container，Delight 在实际主栏宽度 700 / 620 / 430px 处复用现有紧凑布局，修复 860px 视口展开 312px 侧栏后仍保持双栏、内容被挤压的问题；viewport media query 继续独占移动导航切换，侧栏宽度与过渡不变。
- **移动 Web 探针提交状态跨重渲染保留（issue #103）**：兴趣与避雷探针的非聊天动作按归一化 `type + domain` 保留 in-flight 状态；消息层关闭再打开时整卡仍保持禁用与 `aria-busy`，结算成功后才记为已处理，失败则保留卡片并恢复重试，避免重复 POST 或失败后消息消失。
- **移动 / 桌面 Web 已喜欢 Delight 状态一致（issue #104）**：liked 卡片保留结果提示与完整动作组，like 统一为 `aria-pressed=true` 且仅禁用重复提交；本地点击、队列重灌和实时事件会收敛到同一状态，失败点击恢复可重试。
- **扩展 Delight liked 投影补齐（issue #108）**：side panel 新增独立的结果、动作和 like ARIA 投影，服务端 liked 队列状态不再被本地 pending 覆盖，除重复 like 外的查看、保存、负反馈与聊天动作继续可用。
- **桌面 Web 探针反馈明确显示主题（issue #109）**：消息抽屉与画像页的 inline 结果和 toast 统一使用同一条安全文案，显示有长度上限的兴趣/避雷主题，不再只提示泛化的“这个方向”。
- **桌面 Web 主题引擎 oklch 化 + 可调色相（PR #110）**：引入 `--hue-primary` 单一控制点，品牌 / 探针 / 语义色全部经 `oklch()` 从色相派生，新增 `--accent-subtle/light/strong/hover/deep` 五级强调色阶与 `--contrast-strong` 互补聚焦环；设置页新增 12 色块 + 彩虹滑块 + 数值输入的主题色相拾色器，经 `obc.themeHue` localStorage 持久化（页面加载时 inline 脚本恢复，避免闪烁）；所有交互元素统一「hover 强调色发光 / active `scale(0.97)` 加深 / focus-ring 聚焦环」模式，导航图标换为内联 SVG，新增 Bilibili / 小红书精确平台品牌色（经 `data-platform` 定位）。纯桌面 Web 前端改动，扩展 popup 与其它三端不受影响（插件 `web/css/app.css` 为独立文件，本次不同步）。

## v0.3.163 / extension v0.3.163 / desktop v0.3.163：登录状态诚实同步、Web 库存恢复与冷加载判定（2026-07-11）

后端源码走 `backend-v0.3.163`，浏览器插件走 `extension-v0.3.163`，桌面安装包走 `desktop-v0.3.163`。

- **候选评估改为连续补位 worker pool**：runtime 默认期望 3 个、每批最多 30 条的 LLM worker，任一槽位完成即补下一批；batch claim token 与串行 SQLite commit/admission 防止热重载和乱序覆盖。成功缓存后立即以单飞任务触发推荐文案预计算，且不阻塞评估补位；已缓存待文案行计入目标保留量，避免 copy 较慢时过度评估。限流、无 provider 和连续零入池均有有界退避，60 秒只作安全唤醒；`[discovery].candidate_eval_concurrency` 在配置、API、UI 与构造器统一硬限制为 `1..3`，每批 30 条故总 raw 在途最多 90；超范围旧值按既有配置规则回退默认 3。平台抓取节奏、raw ceiling、来源配比和准入阈值保持不变。
- **PC / Web 配置页登录状态改为纯本地、诚实同步**：`GET /api/sources/status` 不再为 Reddit 执行命令探测或访问平台；Reddit 只检查本地 credential，抖音有 Cookie 时显示「状态待验证」，小红书 / 知乎以插件读取真实登录 Cookie 后上报的布尔心跳为准。插件连接本地 runtime stream 时立即刷新 XHS `web_session` 与知乎 `z_c0`，两端配置页统一状态文案，`xsec_token` 明确仅是内容令牌；并发心跳改用短生命周期 SQLite 连接，避免共享连接偶发 HTTP 500。整个状态刷新链路只访问本机后端，不增加平台请求或封控风险。
- **Web 推荐库存可从瞬时失败中自动恢复**：桌面 Web 与移动 Web 不再把推荐或 runtime 状态超时折叠成真实零库存；两类读取独立按 1/2/4/8 秒有界重试，库存事件只唤醒仍为空且上次读取失败的页面，不覆盖已显示或滚动追加的卡片。
- **本地 embedding 冷加载不再误报停服**：readiness probe 区分缓存成功、明确失败与超时；普通 health 只容忍 loopback Ollama 冷加载超时，引导初始化仍严格等待真实向量成功，远端超时、404/500 与空向量继续报告未就绪。
- **偏好反馈跨主题轴生效且操作更明确**：推荐反馈同时匹配细粒度与粗粒度 topic，并保留真实平台来源；桌面、移动和插件端统一兴趣 / 避雷 / 稍后反馈语义，移除会误导用户的推荐纠正入口，同时保持延迟聊天输入聚焦和 embedding fallback 稳定。
- **Chrome Web Store 待审版本可显式替换**：商店发布 workflow 新增默认关闭的 `replace_pending`；仅当上传返回官方 `NOT_UPDATEABLE` 时调用 `cancelSubmission` 撤回旧审核，再重试同一新版上传并提交审核，避免上一版长时间待审阻断紧随其后的修复版。
- **Chrome Web Store 商店页文案与截图已刷新**：短描述和详细描述改为七平台、本地后端、数据默认留在本机的准确定位，移除“至少先登录 B 站”和只列四个平台的过期引导；新增五张 1280×800「品牌首图 + 当前真实界面」素材，依次展示七平台、插件 / PC / 手机三端、跨平台推荐、可纠正画像和诚实登录状态。截图由只允许 loopback 请求的固定脱敏演示服务生成，不读取真实配置、数据库、Cookie 或账号信息，并用 pytest 锁定文件顺序、尺寸和平台覆盖。
- **Chrome Web Store 截图 V2 已完成**：五张浅色、文案偏多且推荐头图为空的素材已精简为三张高对比成品。新增 8 张确定性本地插画封面，七条脱敏推荐和惊喜推荐均经固定假域名 → 本机 `/api/image-proxy` → 真实桌面 / 插件 / 手机 UI 数据链路渲染；Playwright 等待 `<img>` 解码成功才捕获，首图现在完整展示惊喜大头图与推荐卡头图，另两张分别展示三端体验和诚实接入状态。素材测试锁定 640×360 封面、三张 1280×800 成品、文件顺序与零真实用户数据，捕获继续阻断全部外部平台请求。
- **初始化进度回调测试替身对齐**：`SoulEngine.analyze_events()` / CLI init 已传入 `progress_callback`，既有 CLI 与 Soul 单测中的 fake 仍停留在旧签名，导致主分支全量测试稳定报 11 个 `unexpected keyword argument`。测试替身改为显式吞掉额外关键字参数，不改生产初始化、进度上报或画像行为；相关 CLI / Soul 224 项恢复通过。
- **Chrome Web Store listing metadata API 安全自动化**：新增独立 `Update Chrome Web Store Listing` workflow、`chrome-webstore-metadata.mjs` CLI 和 15 项 Node 回归测试。默认只读探测 v1.1 draft，即使 schema 不支持写入也会先输出字段名、长度与 SHA-256；只有 response 实际暴露 `summary` / `description` 和 listing identity 后，显式 apply 才会撤审、allowlist 写入、精确回读并通过 v2 重新提审。Google 官方 v1.1 `Item` resource 未承诺详情页文案字段且 API 将于 2026-10-15 停止支持，因此 schema 不匹配会在撤审 / 写入前安全停止；截图没有公开写接口，仍禁止猜测 Dashboard 私有端点、读取浏览器 Cookie 或声称仓库 PNG 已经上线。测试侧同时移除 B站 content-script retry 用例对“300ms 等待前重试一定尚未触发”的负载敏感假设，不改生产重试行为。

## v0.3.162 / extension v0.3.162 / desktop v0.3.162：跨设备扩展认证、反馈提速、发布时间贯通与 Ollama 自愈（2026-07-11）

后端源码走 `backend-v0.3.162`，浏览器插件走 `extension-v0.3.162`，桌面安装包走 `desktop-v0.3.162`。

- **跨设备扩展访问可控开放**：新增默认关闭的设备密钥认证与 `ext-key generate/enable/disable/list/revoke` CLI；配置只保存密钥摘要，扩展换取短会话后使用 Bearer 访问，远程主机强制 HTTPS/WSS，并在保存 endpoint 前请求最小 host 权限。
- **推荐反馈即时响应且可真实撤销**：桌面推荐卡和兴趣/避雷探针先本地更新，保留 10 秒撤销窗口；换一批先展示新内容、再后台结清旧卡，MMR 与聚类移出 asyncio 事件循环，减少卡顿和旧卡回流。
- **七平台发布时间贯通所有推荐表面**：Bilibili、小红书、抖音、YouTube、X、知乎和 Reddit 的可靠发布时间进入候选池、缓存、普通推荐与惊喜推荐，并在桌面、移动端、插件和 CLI 使用统一的本地相对时间展示；知乎惊喜卡同时补齐正文预览和无封面文字卡。
- **托管 Ollama 与引导初始化更能自愈**：with-embedding 私有 Ollama 记录并复用实际端口/模型目录，崩溃后按 5 秒至 300 秒退避自动拉起；缺失或损坏的向量模型可在引导初始化中自动修复并显示进度。
- **本地 embedding 冷加载不再误报停服**：readiness probe 改为缓存成功、明确失败与超时三态；普通 health 仅容忍 loopback Ollama 冷加载超时，初始化仍严格等待真实向量成功，远端超时、404/500 和空向量继续报告未就绪。
- **推荐准入与 API 兼容边界收紧**：除 `explore` 外所有来源统一执行全局 admission 下限，缓存与展示出口 fail closed；OpenAI Responses 请求固定发送 `store=false`，避免兼容网关拒绝无状态调用。
- **默认 CI 不再被可选浏览器依赖阻断**：issue #98 的真实浏览器 E2E 改为与既有引导 E2E 相同的 `pytest.importorskip` 收集契约；普通 `[dev,x]` 测试环境未安装 Playwright 时跳过 integration 模块，安装 `[browser]` 后仍执行完整浏览器用例。
- **Web 空库存假象自动恢复**：移动 Web `/m` 与桌面 Web `/web` 不再把推荐或 `runtime-status` 超时折叠成真实零库存；两类读取独立按 1/2/4/8 秒最多重试四次，成功空数组才进入真实空态。库存实时事件只会唤醒仍为空且上次读取失败的推荐页，已显示或滚动追加的卡片不会被后台刷新覆盖。

## v0.3.166 原生保存同步补充（2026-07-14）

- **桌面源码 selftest 数据保护**：`packaging/entry.py` 仅在 PyInstaller frozen 运行时执行旧 onedir 安装目录迁移；从源码 checkout 以 `OPENBILICLAW_SELFTEST=1` 配合隔离 profile 自检时，不再把仓库根中 gitignored 的 `config.toml` / `data/` 误判为旧安装数据并移动。真实桌面安装包的历史配置与数据库迁移保持不变。
- **七平台原生保存真实验证与最终修复（issue #56）**：在自动同步保持默认关闭的前提下，先通过推荐卡/保存 API 只落本地，再从收藏页与稍后再看页手动触发；2026-07-14 使用当前登录账号强制清空旧终态后完成 Bilibili、YouTube、小红书、抖音、X、知乎、Reddit 两类动作真实回归。Bilibili favorite/watch-later 均为 `synced`；YouTube Watch Later 与其余六个平台的收藏或 watch-later→favorite fallback 均为 `synced/already_synced`。YouTube favorite 会在多个 exact `OpenBiliClaw` 重复列表中优先采用 checked proof，否则按稳定 DOM 顺序复用一个，不删除账号列表；小红书适配当前 `noteContainer/collect-wrapper`，已有精确页直接复用，没有精确页时用户手动 native-save 可越过后台 discovery tab mutex 并创建精确 tokenized route；知乎适配当前 `Favlists-item` 与异步创建表单。后端 native task/job heartbeat、terminal persistence 与 broker poll 改用短连接/线程卸载并有界重试 SQLite lock，durable terminal state 在 heartbeat completion race 中优先；XHS alarm/WebSocket poll 单飞，避免重复领取后丢任务。开发热更新 endpoint 现在返回 `delivered`，可确认运行时事件是否真的到达至少一个扩展订阅者。
- **六平台原生保存跨层复审修复（issue #56）**：后端、E2E preflight 与扩展共享 validator 统一 URL 契约：YouTube 只接受单值非空 `v`，小红书带 query 时必须有单值非空 `xsec_token`、可选单值非空 `xsec_source`，默认 443 与尾点 host 规范为无端口标准 hostname。`extension_native_save_jobs` 的完整事务改用独立短连接，六 source 并发 poll 不再在共享连接嵌套 `BEGIN IMMEDIATE`。runner 的 global dispatcher mutex 只保护 tab 创建/加载阶段，随后按 task/tab 独立关联并发执行；`chrome.storage.session` 同时记录所有 runner-owned tab，重启恢复逐个定点关闭。Reddit open shadow DOM 若出现多个同名 Save/Unsave 控件固定零点击失败，属于嵌套 post/comment identity 的唯一控件也不会越界成为 mutation target。以上均补先红后绿回归，仍不替代五个平台待新授权的真实验证。
- **六平台原生保存真实首轮与 SPA 兼容修复（issue #56）**：逐平台授权的 favorite 首轮中仅 X/Twitter 得到 `synced`；YouTube、抖音、知乎、Reddit 为 `native_save_failed`，裸 URL 小红书为 `unsupported_content_type`，未据此误报六平台通过。修复按真实失败边界收敛：YouTube / 知乎先只读等待入口、真正打开弹窗最多一次；Reddit 递归 open shadow root 确认 `Save/Unsave`；抖音仅在精确 `/video/<id>`、无其它可见 identity 且全页唯一 `data-e2e=video-favorite` 时启用 route fallback；小红书 job/preflight 只保留/放行打开公开笔记必需的单值非空 `xsec_token/xsec_source`，其它 query、重复和空值仍 fail closed。六平台继续做 mutation 前最长约 10 秒的业务 readiness polling。以上均有先红后绿 fixture；五项修复尚未获 fresh authorization 重跑，Reddit 首轮 2xx 后确认失败使平台状态不确定，禁止无新授权重试。
- **六平台原生保存安全 E2E 契约（issue #56）**：新增默认不执行账号写入的授权 harness/runbook；trusted-local `/api/extension/e2e/run` 增加与 generic actions 互斥的 dedicated native-save 模式。YouTube / 小红书 / 抖音 / X / 知乎 / Reddit 的状态变更只有在 `allow_state_changing=true` 与 exact platform/action/public content ID/expected target 同时存在且符合六行映射时，才经单 item `/api/saved/{action}/sync` 和绑定 exact task/item/URL/resolved action/target 的 durable broker 执行。通用捕捉 E2E runner 即使 envelope 有效也拒绝 favorite/bookmark，避免点击与命名内容脱钩；authorization/result envelope 中的任何账号 ID、Cookie、token、HTML、响应正文、含秘密 URL 或多余字段均 fail closed（小红书 public-note 导航 token 只存在于 exact extension job）。dedicated callback 只允许 `platform/action/content_id/expected_target/task_status/error_code` 和固定 error-code allow-list；runbook 锁定自动同步默认关闭、手动 favorite/watch-later、显式自动同步同意、duplicate→`already_synced` 和本地 cleanup 不删除平台记录。本条不包含真实账号验证、release header 或发布。
- **三端原生保存改为后端状态驱动（issue #56）**：插件 side panel、桌面 Web 与移动 Web 不再把非 B 站来源永久写成 local-only，也不复制平台路由矩阵；六平台保存项统一解释后端 `sync_status/sync_task_id/resolved_target/error_code`。`pending + 空 task_id` 保留手动同步，带 task ID 的 pending / syncing 显示可访问的禁用状态；只有 `unsupported_content_type` 无同步按钮，`unsupported_adapter_missing` 保留滚动升级重试，`extension_required` 给出连接登录态插件指引，成功态展示真实 resolved target。默认关闭自动同步与收藏/稍后页批量按钮保持不变。
- **六平台扩展原生保存 adapter 注册（issue #56）**：YouTube / 小红书 / 抖音 / X / 知乎 / Reddit 现在按明确能力/目标矩阵注册 production adapter，统一委托热重载期间保持稳定的 `ExtensionNativeSaveBroker`；Bilibili direct adapter 不变。旧六平台 bare `unsupported` 行经窄命名迁移改为 `unsupported_adapter_missing` 并可进入单项/批量重试，Bilibili、未知平台和 `unsupported_content_type` 永不改写。broker wake best-effort 发布既有 `<slug>_task_available`；本阶段建立 adapter 注册，executor 接线状态见后续条目。
- **扩展原生保存共享 contract/runtime 基础（issue #56）**：新增与后端完全对齐的六平台 platform/slug/HTTPS host task guard、固定 status/code/message sanitizer、`NATIVE_SAVE_EXECUTE` / `NATIVE_SAVE_RESULT` content runtime 和复用全局 dispatcher mutex 的 active-tab runner。runner 只接受正确 tab + platform + task UUID + item key 的首个结果，通过调用方提供的 authenticated closure 回传；timeout 固定 `failed/native_save_timeout`，只重试 content readiness、不重放 mutation，并在 finally 关闭 tab、移除 listener、释放 mutex。该条建立共享基础，平台接线状态见后续条目。
- **Reddit Saved / X Bookmarks 扩展执行链（issue #56）**：Reddit/X executor 已接入 `NATIVE_SAVE_EXECUTE`、authenticated source dispatcher、alarm 与 runtime-stream wake。Reddit 仅接受 post/comment fullname，在完整页面 token contract 可用时同源调用 `/api/save`，否则点击目标的可见 Save 并确认 Unsave；X 仅接受数字 status ID，点击目标 tweet 的 `bookmark` 并确认 `removeBookmark`，不固化 GraphQL query ID。runner 仅把自有 tab 数字 ID 写入 `chrome.storage.session`，MV3 worker 启动时只回收该记录对应的 orphan tab。该阶段两平台尚未真实账号验证；后续接线状态见 YouTube 条目。
- **YouTube OpenBiliClaw / Watch Later 扩展执行链（issue #56）**：Reddit/X 与 YouTube executor 已接入 `NATIVE_SAVE_EXECUTE` 与 authenticated `/sources/yt/task-result` 回传，并复用共享 MV3 recovery barrier。YouTube 接受 canonical watch/shorts task URL，也可将无 query 的 `youtu.be/{id}` task 与安全重定向后的 watch/shorts 页关联；只允许单一一致的 video ID 与窄列表重定向 query，拒绝未知 query、凭据、fragment 和重复 `v`。favorite 精确区分 Unicode 大小写匹配 `OpenBiliClaw`，不存在时创建后 close/reopen 再查询，创建失败或重查不匹配绝不写其它列表；Watch Later 只接受精确平台 playlist ID `WL`，两条路径都以 checked membership 确认成功。既有 Reddit 2xx 未确认保护与 X 结构化 rate 判断保持不变。三平台仅 fixture 测试、尚未真实账号验证；小红书、抖音、知乎 executor 仍待接，未宣称六平台全部完成。
- **小红书 / 抖音原生收藏扩展执行链（issue #56）**：小红书与抖音 dispatcher 现在在 legacy search/bootstrap payload 解析前识别 exact `native_save` union，经共享 MV3 recovery barrier、active-tab runner 与 authenticated `/sources/{xhs,dy}/task-result` 回传；原有 debug/bootstrap/discovery 路径保持不变。executor 只接受与 task/item/current page 严格关联的小红书 note/video 或抖音 aweme/video identity，先判可见登录 overlay、可见 deleted/profile/creator，再校验 favorite 或 watch-later→favorite 的精确目标；隐藏 SPA 模板不会误判，已选中返回 `already_synced` 且零点击。当前 live page 未暴露完整稳定的同源收藏 request contract，production 因而只在唯一可见、带 exact content-ID 的详情容器内点击精确收藏控制，并要求控制的最近 identity ancestor 正是该容器；ancestor 的 hidden/inert/aria/style/computed visibility 任一隐藏都会排除，嵌套推荐卡、隐藏 dialog 和全局同名控制均不会成为 mutation target。request 明确拒绝或缺失后回退点击前会再次查询并复核 selected，等待期间已被用户/平台选中的项目直接 `synced`、不反向 toggle；fixture 另锁定 request success、429 与网络不确定后禁止 fallback。risk UI 同样只在目标详情容器内按具体事件 identity 比较动作前基线；比较为 directional after-minus-before，旧事件的移除/重排及全局无关 toast 不阻断，只有动作后的目标内新结构化提示且未确认 selected 才映射 `rate_limited`。两平台仅 fixture 测试、未做真实账号写入验证；至此 5/6 executor 已接，知乎仍待接。
- **知乎 `OpenBiliClaw` 收藏夹扩展执行链（issue #56）**：知乎 dispatcher 现在在 legacy bootstrap/discovery 解析前识别 `native_save`，复用共享 MV3 recovery runner，并通过 authenticated `/sources/zhihu/task-result` closure 回传。executor 只接受严格 numeric `question:<id>` / `answer:<id>` / `article:<id>`，同时关联 task URL、当前页、item key 与 content type；favorite 和 watch-later fallback 都只允许 resolved favorite / exact `OpenBiliClaw`。可见登录 overlay、已删除/不可用、extra-colon、profile、身份或目标不匹配均在 mutation 前失败。收藏控制、刚打开的 dialog 与 exact row 绑定唯一目标 identity 和最近 identity fence，完整祖先可见性排除隐藏模板、关联卡、隐藏/错项 dialog；标题按 Unicode 大小写精确匹配，同名重复安全失败。缺失收藏夹时只创建一次 exact title，随后 close/reopen/re-query，创建失败或重查不一致绝不 fallback；checked 已存在返回 `already_synced` 且零点击，创建后未勾选会精确选择并确认 checked。rate/risk 只读取动作目标局部事件并做 directional after-minus-before；selected proof 优先，stale、无关、嵌套推荐与事件重排不会误报。至此六平台 executor 6/6 已接并完成 fixture 自动化，但六平台均未做真实账号写入验证；没有持久化 request、token 或不确定结果自动重试。
- **Reddit/X 原生保存复审修复（issue #56）**：module startup、install/startup lifecycle、alarm、runtime wake 与直接 runner 共用一个共享 MV3 recovery barrier，旧 orphan 清理完成前不会领取新任务；`chrome.storage.session` 缺失或异常时安全降级为无持久恢复。Reddit 的网络不确定或 2xx 未确认请求只执行一次并固定失败，绝不回退可见点击；comment permalink 与目标 DOM 关联收紧。X rate 判定在动作前只读目标 tweet 内的结构化状态，动作后另接受目标邻近状态或相对基线新出现/更新的平台 toast，不会被全局旧错误或 stale toast 误拦截；已存在或点击后出现的 `removeBookmark` 是确定性已收藏证据，优先于同时存在的 rate UI。该阶段两平台仍仅 fixture 验证、尚未真实账号验证；后续接线状态见 YouTube 条目。
- **扩展原生保存 runtime 生命周期复审修复（issue #56）**：共享 mutex 改为 legacy dispatcher 已使用的 `globalThis` keys；claimed job 等锁、tab load 与执行共用单一 deadline，busy 超时只回传一次且不开 tab。backend/extension 统一 canonical HTTPS URL（默认端口、无 fragment/额外 query），执行前复核 final tab，结果同时绑定 sender URL。content dedupe 改为 256 项 bounded outcome-promise cache，重复消息重放同一 sanitized 结果；create/get/send/post/listener/tab cleanup 异常互不阻断，load listener 先注册再复核，关闭更新竞态。该条完成共享 runner 复审，平台接线状态见后续条目。
- **扩展原生保存迟到 tab 与 listener 清理收口（issue #56）**：absolute deadline 已先返回 timeout 时，后续才 resolve 的 `tabs.create()` success 会单独回收且不会与正常 finally 双删；tab-update listener 的 add-after-register / transient remove 异常也通过 registration fence 与独立 retry 回到 baseline。Chrome mock 恢复进入测试前的 global mutex keys；文档明确 256 项 replay 是 recent window 而非永久 once。MV3 service worker 重启不会自动关闭 task tab，后续接线必须先 reconcile orphan tab。
- **六平台扩展原生保存 claimed 生命周期修复（issue #56）**：仍为 pending 的 broker job 随调用方取消安全写为 `cancelled`；扩展已 claim 的 `in_progress` job 在 service deadline 或 runtime rebuild 取消后继续等待 durable 终态，由原 `SavedSyncService` detach watchdog 跨 service/router 替换保持续租并落回原 task snapshot，不写伪 `interrupted`、不创建第二次平台 mutation。六组 adapter definition slug 与实际 broker wake slug 新增矩阵契约；重复 mapping 保留为最终 review 关注项。

- **扩展原生保存 source task multiplex（issue #56）**：`/api/sources/{xhs,dy,yt,x,zhihu,reddit}` exact endpoints 现在优先领取 `type=native_save` broker job，再保持既有 discovery/bootstrap queue；callback 先查 global durable ownership 阻断所有 legacy namespace fallthrough，再按 exact slug 分流，连 intentional native/legacy same-UUID cross-slug collision 也不会改写任一 row。broker/DAO completion 同时绑定 exact platform slug，跨 source callback 固定 409；五个 legacy handler 也在解析 notes/videos/items 前先完成 native 判别，使 native payload 的无关 `null` 字段进入严格 422 而非 500。新增 X 的 next-task/task-result/kick 同形路由；该条建立 source multiplex，平台 executor 接线状态见本版本上方条目。
- **扩展原生保存 durable broker 基础（issue #56）**：新增独立 `extension_native_save_jobs` SQLite ledger 与 `ExtensionNativeSaveBroker`，为 YouTube / 小红书 / 抖音 / X / 知乎 / Reddit 后续登录态扩展执行提供持久化、可恢复的 sanitized job contract。active job 按 platform + item + requested action 原子复用；同一 dispatch deadline 覆盖 best-effort wake 与 polling，未 claim 超时持久化 `extension_required`，已 claim lease 超时固定 `failed/extension_task_timeout` 且不自动重放。callback 同时校验 UUID 与 item key；扩展 code 使用显式集合，message 只落后端固定文案，Unicode category-C、Cookie/token、HTML 与原始响应不会进入 SQLite。该基础阶段未注册 adapter；当前 adapter 与 executor 接线状态见本版本上方条目。
- **跨平台原生保存设计、首阶段计划与本地存储 / 编排 / API / 三个图形化保存界面基础（issue #56）**：所有保存 local-first；favorite 写平台收藏，watch-later 优先原生、缺失回退收藏，自动同步默认关闭。canonical identity、normalized 保存表、capability router、durable sync service、B 站 adapter、平台中立 `/api/saved/*` 与 task polling 已落地。插件 side panel、桌面 Web 与移动 Web 现在统一发送 canonical payload；CLI 只通过 `config-show` 显示开关。收藏 / 稍后再看页提供单项重试、批量确认、真实目标与 `待同步 / 同步中 / 已同步 / 需要登录 / 同步失败` 状态，按平台汇总结果。自动同步首次开启必须确认账号写入警告；手动同步始终可用，平台失败不回滚本地，删除本地记录不删除平台记录。「全部稍后再看」用 local-save `Promise.allSettled`，只移除本地成功项并显示精确成功 / 同步中 / 失败数。每次 sync 在单个事务写 durable task 快照并领取 live owner；严格 identity、热重载取消和 heartbeat/watchdog fence 语义保持不变。
- **原生保存 UI 复审修复（issue #56）**：三端 recommendation / delight 保留完整 canonical five，保存状态、mutation fence 与桌面 delight cache 统一按 `list_kind:item_key`；URL fallback 的「全部稍后看」使用服务端返回 key 且失败项留队。桌面 saved 请求改为有界 `requestJsonStrict`，HTTP / 网络 / 超时进入现有回滚。手动同步不再 20 秒后把非终态汇总成 `0/N`：task ID 持续后台轮询，按页面可见性调节间隔，短暂 abort 后可恢复，超过前台等待窗口明确显示「仍在后台同步」。三端刷新失败保留最后成功列表 / 总数并提供内联重试；重渲染按 item/action 恢复焦点。移动设置改为模态对话框，支持 Escape、焦点闭环、返回 opener；配置 GET 失败前保存保持禁用并可重试。
- **原生保存 UI 二次复审修复（issue #56）**：插件与移动 Web 的 saved save/remove/list/status/sync/task poll 及 config GET/PUT 全部使用有界 Abort timeout，超时 mutation 必定释放 busy，poll 超时保留 recoverable task。三端每次列表成功加载都会从持久化 `sync_task_id` 去重恢复非终态任务，并用 task→item ownership 把关联项显示为同步中、排除重复单项/批量提交；页面隐藏后可恢复，page teardown 会清理 timer。同步或移除导致原控件消失时，焦点依次回退到下一/上一卡片动作、列表同步/重试、可聚焦标题。插件与桌面 Task 8 保存按钮在 coarse pointer 下达到 44×44，sync label 预留稳定宽度；桌面 delight tooltip 与 pressed 状态一致。
- **原生保存 UI 最终复审修复（issue #56）**：插件 saved/config 请求的同一 Abort deadline 现在覆盖后端地址解析、初次设备会话交换、受保护请求、401 强制换票与响应解析；认证 fetch 接收同一 signal，5ms 回归证明初次交换和强制刷新都可被真实中止。插件、移动 Web、桌面 Web 的批量同步与重试加载会在刷新前捕获列表级焦点 token，重渲染后优先回到同一列表动作，再退回卡片动作与标题。
- **原生保存复审收口**：所有 saved/task/legacy 输出在截断前统一移除 adapter-controlled result 文本中的 Unicode category-C 字符（含 U+202E、U+200B、U+0085）；active auto-sync 期间重复保存返回新 no-op task 的完整 `failed/sync_already_in_progress` 快照，不再拼接旧 owner 字段。durable task ledger 当前明确为数据库生命周期保留、无自动 pruning；有界清理因缺少保留窗口/容量契约延期设计。
- **原生保存自动同步配置契约（issue #56）**：默认关闭的 `[saved_sync].auto_sync_enabled = false` 支持 TOML、`GET/PUT /api/config`、`config-show`、插件设置、桌面 Web 与移动 Web 严格 round-trip；开启只创建后台 sync、不阻塞本地保存，手动 `/sync` 始终可用。
- **原生保存 Phase 1 文档与验证收口（issue #56）**：配置、CLI、存储迁移、runtime、integration、推荐、扩展和四份架构图统一为当阶段 local-first 契约；B 站目标统一为「B站稍后再看」/「B站 OpenBiliClaw 收藏夹」。当阶段只有 Bilibili 注册真实账号写入 adapter；六平台 adapter 的后续注册见本版本上方条目，扩展 executor 仍未实现。全量验证补齐 canonical `content_cache.item_key` 夹具，并修复 desktop 共享 normalizer 对 `x → twitter` 的别名归一化，以及移动设置加载成功后被 `.btn` 样式错误显示的「重试加载」。默认 smoke 和本地视觉 E2E 不发送 favorite / watch-later 请求；命名 BV ID 的真实写入必须等待当次明确授权或测试账号，执行时必须轮询终态并恢复原自动同步配置。当次授权真实账号 E2E 已验证手动收藏、手动稍后再看、开关开启后自动收藏三条 `synced` 路径，并独立确认本地删除不删平台记录、开关恢复为默认关闭。Runbook 同步规避 macOS Bash 3.2 在 `set -u` 下展开空 Header 数组的兼容性失败。
- **原生保存整分支复审修复（issue #56）**：封面生命周期查询改用 canonical `saved_memberships` 保护 B 站及跨平台已保存封面，并添加 `saved_memberships(item_key)` 关联索引，避免 normalized 保存因缺少 legacy BVID 行被误清理或漏预取。插件、移动 Web 和桌面 Web 现在把后端 `unsupported` 终态如实显示为「仅本地保存 / 暂不支持平台同步」，不再提供永久无效的单项重试或计入批量同步数量。
- **七平台真实保存验证修复（issue #56）**：使用真实 `content_cache` 候选逐平台验证收藏 / 稍后再看时，发现知乎真实保存 ID 为 `question:<id>` / `answer:<id>` / `article:<id>`，旧 API 把所有含冒号 `content_id` 一律返回 422。现在仅精确放行这三种知乎 typed numeric identity，仍 fail closed 拒绝未知类型、空段、URL fallback 伪装和其它平台额外冒号。修复后七平台真实候选的两个本地列表均可建立；非 B 站手动同步如实终态为 `unsupported`。
- **Canonical identity 贯通发现、推荐与保存列表输出（issue #56）**：`DiscoveredContent` 派生稳定的 `item_key`，可从受支持平台的 namespaced storage key 恢复来源与 raw ID；非 B 站缓存主键使用平台命名空间、B 站继续保留 raw BV 存储键兼容旧 join。`content_cache` / `recommendations` 新增并回填 indexed `item_key`；升级时同一 canonical identity 的旧重复缓存行会优先保留 canonical storage-key 行、合并非空元数据，并在删旧 cache 行前为尚无 `item_key` 列的 legacy 收藏 / 稍后看表补列、重定向稳定身份，随后 normalized saved migration 通过该键关联 keeper，避免列表项误降级成 `bilibili:<legacy-id>`。推荐历史的旧 raw-ID fallback 只在 cache 唯一命中时关联，推荐、delight、收藏与稍后看列表统一输出 `item_key/content_id/source_platform/content_url/content_type`，且非 B 站内部键不会被拼成 B 站 URL。
## v0.3.161 / extension v0.3.161 / desktop v0.3.161：Keyword inspiration 轴库 + 搜索词生成模式选择器（2026-07-09）

后端源码走 `backend-v0.3.161`，浏览器插件走 `extension-v0.3.161`，桌面安装包走 `desktop-v0.3.161`。

- **guided init 过程可见性（init-progress-visibility）**：初始化进度不再全程钉死在 13%/38%/63%/88% 静止刻度。后端数据面：`InitCoordinator` 新增阶段子进度 `stage_progress()`（clamp、`total≤0` 忽略、随 `init_progress` 事件下发）、心跳 `touch()`（只落库不发 SSE）、阶段 `eta_seconds`（`{1:90,2:180,3:70,4:120}`）与 `get_status().last_activity`；`/api/init-status` 新字段全部 optional 向后兼容；API wrapper 跑 30s 心跳 task 兜底活性。生产者：阶段 1 逐源上报「正在采集 <平台>」，阶段 2 经 `analyze_events(progress_callback=…)` 每完成一个分片推进一次（CLI 路径打印逐批完成，prompt-cache 约定不破）。三 GUI 面（popup / 桌面 Web / setup 向导）同构升级：分片实进度或 elapsed/eta 伪进度（封顶 0.95）、per-run 单调 clamp 永不回退、子进度批次文案、`>90s` 无后端活动转 amber「后台已 N 分钟没有新进展」停滞提示（阈值 = 30s 心跳 × 3）、「整个过程通常需要 2–5 分钟」预期文案；运行中刷新 / 重新打开页面(桌面 hydrate 与 popup boot 分支)会重新挂载实时进度轮询,不再停在加载那一帧(此前只有 SSE 事件或点击能启动轮询,而心跳按设计不发 SSE,后端挂起时轮询是 last_activity 的唯一观测者);旧后端 / 旧扩展互不影响。
- **安装脚本复用 cookie 即时校验**：`--reuse-from` 复用的 B 站 cookie 在后端健康后立即消费 `/api/init-status` 现成的 `prerequisites.bilibili_check` 真实探测（不新增第二条校验路径）：确认失效 → `missing` 追加 `bilibili.cookie (stale — reused cookie failed live validation)`、最终状态从 `complete` 降级 `needs_secrets` 并阻止注定失败的自动 init；`install.sh` / `install.ps1` 对确认失效打印「重新登录由扩展同步」明确文案，探测不可达 / 未定时保留原免责声明。过期 cookie 从「init 跑 30s 后 empty_history」提前到装完即报。
- **PC / Web 配置页来源登录态改为纯本地、诚实同步**：`GET /api/sources/status` 不再运行 Reddit `rdt` / `opencli` 探测，也不访问任何平台；Reddit 只检查本地 credential 文件，抖音有 Cookie 时标为「状态待验证」而非已接入，小红书 / 知乎以插件最近上报的布尔登录心跳为权威（未上报 / 显式登出 / 新鲜登录 / 过期分别展示），知乎仅在从未收到心跳时回落任务历史。插件 background 每次连接本地 runtime stream 会立即读取一次小红书 `web_session` 与知乎 `z_c0` 是否存在并只上报布尔值，不打开或刷新平台页面；两套配置页统一 `ready / unverified / login_required / error` 文案，`xsec_token` 明确标为内容访问令牌、不代表账号登录。状态页的 30 秒刷新仍只请求本地后端，不增加外站请求或封控风险。真实 unpacked Chrome E2E 还发现两个心跳会并发进入 FastAPI 线程池；登录态读写现改用各自短生命周期 SQLite 连接，由 busy timeout 串行化，避免共享 connection 偶发 `InterfaceError` / HTTP 500。
- **多平台发布时间补齐（issue #75 后续）**：Bilibili、小红书、抖音、YouTube、X、知乎和 Reddit 的可靠发布时间现贯穿统一候选池、缓存与推荐/惊喜出口；精确时间按本地相对日期展示，平台仅提供相对时间时保留原文，缺失时隐藏。旧缓存不联网回填，投币数明确不做。
- **跨平台原生保存设计与首阶段计划（issue #56）**：确认收藏与稍后再看的统一路由契约——所有动作先写 OpenBiliClaw 本地；收藏同步到各平台收藏 / 书签 / Saved / 专用播放列表，稍后再看优先平台原生能力、缺失时降级到收藏；自动同步总开关默认关闭，本地收藏页与稍后看页保留显式手动同步和逐项结果。设计同时定义 `source_platform + content_id` 内容身份、平台能力路由器、专用 `OpenBiliClaw` 收藏夹、插件任务端点、失败不回滚本地及七平台真实账号 E2E 边界；实施按「通用基础设施 + B站首适配」先行、其它平台在契约实测稳定后分计划接入。
- **知乎惊喜卡正文与无封面体验收尾（issue #79）**：桌面 Web 惊喜推荐现在保留后端已下发的 `body_text`，在标题、互动指标和推荐原因之间展示最多 5 行正文预览；仅在真实溢出时出现可键盘操作的“展开正文 / 收起正文”。无封面或封面加载失败时，左侧媒体区改用正文开头与来源徽章组成的毛玻璃文字卡，不再只显示空泛渐变；切换候选和空队列会恢复折叠态，现有聊天输入保护、反馈和队列行为不变。
- fix: with-embedding 私有 Ollama（11435）纳入自愈/一键修复 + 托管 daemon watchdog 崩溃自动拉起（5s→300s 退避、连续 5 次失败放弃直至手动修复；supervisor 记录 `(proc, host, models_dir)` 启动规格，restart 永远复用记录端口与模型目录，boot 时对私有端口残留 daemon 做记录式收养；私有 daemon 硬设 `OLLAMA_KEEP_ALIVE=24h`）
- 修复 Issue #91：推荐反馈同时作用于细/粗 topic 且保留真实平台来源；三端兴趣/避雷
  操作改为明确文字并补齐 defer，推荐区保持原布局，不新增画像或对话引导入口。
- **桌面 Web 反馈响应与换批卡顿修复（issue #98）**：普通推荐卡与正向/避雷探针现在先在本地即时更新，并保留 10 秒真实撤销窗口；撤销会取消尚未发出的写请求，提交失败会恢复原状态，页面离开时用 keepalive 结清待提交动作。探针聊天继续即时发送，不进入可撤销状态机。`换一批` 改为先请求并展示新卡，再后台把旧卡记为 dismiss；请求显式携带当前可见内容 ID，推荐引擎扩大候选窗口并在平台保底后再次执行排除，避免旧卡回流。MMR 排序和 supergroup 两两聚类改由工作线程执行，保持输出确定性并避免 CPU 循环占住 asyncio 事件循环。
- **惊喜推荐封面不再被空值抹掉 + popup/桌面补来源平台标识**：实测惊喜大卡「互动数据齐全但封面空白且无平台标识」。根因两层：`cache_content()` upsert 对 `cover_url` 无条件覆盖（旁边 `author_name` / `body_text` 早有 `COALESCE(NULLIF(...))` 保护），任何带空封面的重摄入——互动数据刷新、事件驱动 related-chain、插件在 B 站搜索页图片懒加载完成前抓到的卡片——都会把好封面永久抹掉，现已同策略保护；插件采集侧同步拒绝 `data:` 懒加载占位图。平台标识：桌面 Web 惊喜卡徽章原先写在「无封面提前 return」之后随封面一起消失，改为始终渲染；空惊喜队列保持 no-op，不会因读取空候选中断首页 hydration；插件消息流惊喜卡与 delight banner 新增平台 chip（`platformDisplayName`）。四表面契约：本次补齐 popup + 桌面 Web，移动 Web 已有 `card-source` 角标，CLI 文本输出不适用。
- **池子整理不再清零互动数据**：`classify_pool_backlog()` 经 `_rows_to_discovered` 读行、`to_cache_kwargs` 回写——mapper 只回读 view/like/danmaku 三个字段，其余七个互动字段（favorite/collect/comment/share/reply/retweet/bookmark）与 `author_name` 在 dataclass 默认值上被回写清零/置空（与封面被空值抹掉同族的「重写路径丢字段」缺陷，封面排查时顺藤发现）。mapper 现回读全部互动字段，新增 row→dataclass→cache_kwargs 往返保真回归测试。
- **guided init 向量模型自愈与 popup 进度对齐**：从遗留分支 `db726daa` 手工移植仍有效能力；仅当本机 loopback Ollama 诊断为 `model_missing` / `model_broken` 且磁盘空间充足时，`POST /api/init` 才会单飞自动拉取并在 409 detail 返回实时进度；popup init checklist 现在显示进度条与修复按钮。`describe_llm_failure` 同步补齐 auth/401 与 quota/429 可操作说明；`bc2dc983` 已用 LLM 层翻译取代遗留分支的 reason-code 分类结构，因此后者未移植。四表面契约：popup 与已有 desktop Web `/setup/` 覆盖图形进度，CLI init 沿用日志输出，移动 Web 无 init 面板，后两者不适用。
- chore(dev): scripts/release.py 版本一致性检查/升版工具 + release/writing-specs 项目技能 + CLAUDE.md 防坑规则（自提交史提炼）
- ci: PR/main CI 新增 `scripts/release.py --check` 版本一致性强制拦截（stdlib-only，装依赖前秒级 fail-fast）
- **平台接入指南回灌 2026-07 主干演进**：`docs/platform-source-integration.md` 补齐统一 admission policy（`discovery/admission.py`）、keyword inspiration axis 双轨生成、engagement 六项计数契约及当前 `share` 展示缺口、真实登录 cookie 优先 / 任务历史兜底链路、插件任务端点路径形状与鉴权约束、封面代理白名单（`ALLOWED_IMAGE_HOST_SUFFIXES` / CN CDN direct-fetch）、移动端 App deep-link host/path 解析分支、自定义 recipe 的 `AdapterRegistry` 边界及尚未接入运行时 `resolve()` 的现状、GHCR backend + 独立 baked-embedding Ollama 镜像发布渠道；`add-platform-source` skill 同步约束到 `.claude/skills/`（`.gitignore` 放行该子目录），两份 skill 入口保持一致。
- **Responses API 无状态兼容修复（issue #95）**：`api_flavor="responses"` 此前未在请求体顶层显式发送 `store`，导致由 ChatGPT/Codex Responses 端点驱动的兼容网关拒绝请求；现在官方 OpenAI 与 OpenAI-compatible provider 的每个 Responses 请求都固定发送 `store=false`，保持无状态且不改变 Chat Completions、配置或既有重试行为。新增 provider 回归断言，锁定该请求字段。
- **前端 UI 体验优化与扫码入口降噪（PR #97 接手修复）**：桌面 / 移动端 `delight` 队列新增自动轮播、拖拽切换、首尾循环和高度/淡入动画，桌面端使用当前封面作弱化背景；自动轮播现在复用输入保护，用户正在惊喜卡聊天、聚焦或有草稿时不会切卡串反馈。桌面 Web 滚动自动加载新增 1px 稳定哨兵、300px 预触发范围和 scroll / render / runtime 状态重检，避免哨兵已相交但首次被库存或渲染 guard 拦住后不再触发。插件手机版二维码（Issue #96）改走轻量 `GET /api/qr-info` 只取 `lan_ip`，不再触发 `/api/health` 的 embedding readiness probe；该端点在 auth 开启和 degraded 模式下保持公开。补齐 QR/auth/degraded、PC 自动加载与 delight 自动轮播回归测试，并清理 `.gitignore` 噪音和 CSS whitespace。
- **Docker 发布补丁**：`openbiliclaw-ollama` 多架构镜像构建时对 `ollama pull bge-m3` 增加 3 次有界重试，并把重试成功条件扩展为“pull 成功且 allowlist digest 对应 blob 已落盘可见”；每次重试前都会确认 `ollama serve` 仍在响应，若进程退出或不响应则重启，避免 GitHub runner / 上游模型下载偶发 `unexpected EOF`、pull 成功后 store 可见性滞后或 server 中途退出直接打断 Docker 渠道发布；发布契约测试覆盖该构建顺序。Douyin runtime source selection 拆分局部变量名，修复 `main` CI 的 MyPy literal tuple 推断误报。
- **配置页新增「搜索词生成模式」下拉（经典 / 混合 / 灵感，两端一致）**：桌面 Web `/web` 与插件 popup 设置区把 `inspiration_search_enabled` / `inspiration_replace_merged_keywords` 两个布尔收成单一「搜索词生成模式」下拉，三档 经典 / 混合 / 灵感（option value `legacy` / `hybrid` / `inspiration`，两端值 / 顺序 / 文案一致，附「混合最贵」成本提示）。这是 UI/API 派生便利层——`DiscoveryConfigOut` 读出派生的 `keyword_generation_mode`（读容忍：`enabled=false` 一律 `legacy`），`PUT /api/config` 把它翻译回两布尔并规范化（每档显式写两布尔、不留 `replace` 残留），`config.toml` 仍只存两布尔（零 schema 改动、零后端行为改动）；非法值 → 422，mode 与显式布尔冲突时 mode 赢。（`_run_explore_inspiration_stage`），与 regular 通道并存。**种子 = merged call 现成的跨域 `explore_domains`（不是 like 二级兴趣、不是历史旧域）**，喂进 E0 参数化后的核心 pipeline；system prompt 增一条**静态** explore 规则（带 `explore_request` 时 `core_concept` 锚定"未覆盖但相关"的跨域具体实体、避开 `avoid_covered`，仍过 byte-identical cache），F1/F1.5/F2/F3 全继承。新轴打 `source='explore'`，复用 Phase 2 按 `axis_id` 的 yield 回填（零新逻辑，cohort 不排除 explore-kind 行）让高产 explore 轴 yield 上升，下一轮 `list_inspiration_axes_by_source('explore', min_yield=…)` 把它们作为当前域的 `existing_axes` 喂回，构成**舒适区扩张闭环**。装配前按归一化 `interest` 钳制候选 ∈ 当前 domains（机制保证 explore 词 `source_interest ∈ 当前域`）。**默认开**：`inspiration_search_enabled=true` 且 explore 到期即走富链路；富生成 degraded → **降级回旧拍平**（merged domains 现成、explore 池不裸奔），到期轮只多一次 explore 富生成调用、regular 通道不变；`replace` 模式 explore 路径不变。
- **Keyword inspiration 多平台丰富度修复（Phase 2.1）**：真机发现平台数越多（6 平台 = 单次调用 48 槽），`core_concept` 会退化成"话题名 + 平台后缀"（`新游推荐 盘点`）而非具体锚点（`士官长 登陆PS5`）。三管齐下修复：**(F1)** `_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT` 新增静态具体性规则（`core_concept` 须锚定 `fresh_evidence` 里的具体实体 / 事件 / 作品 / 人物 / 机制、禁止复述 interest 或 axis_label、无锚点才退回话题级，仍过 byte-identical cache）；**(F1.5，主)** `materialize_platform_keywords` 装配排序键加确定性 `is_specific` 信号，具体候选同槽位压过泛化候选（`is_specific` 用剥离残留法按最长优先子串移除 interest / axis / marker，正确处理无空格 CJK 如 `新游推荐盘点`→复述），配套 `restatement_rate()` 观测；**(F2)** 单次调用 `max_tokens` 随槽位放大 `min(16384, 8192 + max(0, slots-12)*256)`（6 平台 24 槽→11264、48 槽→16384），provider max_tokens 报错时降回 8192 floor 有界重试一次，不破坏"一轮 ≤1 次成功生成调用"不变式；**(F3)** `RealizedKeyword.metadata` 观测性增补 `core_concept` / `decoration` 并让 preview 报告显式回写 `metadata_by_platform`（只观测、不改最终 keyword 文本与入池）。
- **Keyword inspiration 默认档位提升为 `high`**：`inspiration_breadth` 发布默认从 `medium` 改为 `high`（更宽的素材 / 轴 / 关键词产量：每平台上限 16、采样 8 个二级兴趣、probe 搜索翻倍）。开启 inspiration 的用户升级后不显式设档位即走 `high`，每轮真实 probe 搜索与 LLM 用量随之放大；成本敏感可显式设 `medium`（逐项等于收敛前默认）或 `low`。
- **Keyword inspiration 轴库 Phase 2：yield 学习闭环 + 生命周期 + config 收敛（13→4）+ 编排抽取 + 可选 embedding 近邻合并**：轴库从"能复用"变成"会学习"——production stage 取轴前先跑纯 SQL 的 `backfill_inspiration_axis_yield()`（trailing-window 全量重算 / 幂等 / `yield_score=(admissions+0.3)/(window_uses+1)` Laplace 平滑）+ `apply_inspiration_axis_lifecycle()`（active→stale/retired→90 天 purge），6h 节流、preview 永不触发，排序 prior 地板改为只保护从未消费过的轴（坏轴按真实分下沉）；新增 `window_uses` / `yield_backfilled_at` 两列与 retire（`window_uses>=5` 且 `yield_score<0.08`）/ purge / throttle 阈值常量；13 个 `inspiration_*` 旋钮收敛到 4 个（新增 `inspiration_breadth` low/medium/high 档位派生，medium 逐项等于旧默认，删除键经 diagnostics 通道给出移除提示）；①–⑥ 编排抽成独立 `runtime/inspiration_pipeline.py::InspirationKeywordPipeline`（行为逐字不变，planner 保留四个兼容委托）；可选 embedding 近邻轴合并在 pipeline 层解析 cosine≥0.92 的同兴趣近义轴并入既有 `axis_id`（DAO 保持同步零 I/O），服务超时 / 不可用无损降级回字符串行为并标 `axis_embedding_degraded`。
- **Keyword inspiration 轴库重构收口**：regular/shared inspiration stage 从 `brainstorm → curate → repair` 改为 `select → probe → ground → single-call → assemble → writeback`；常规路径从最多 5 次 LLM 调用收敛为 1 次 `discovery.keyword_inspiration`，新增 `discovery_inspiration_axis` 轴库复用、coverage-first 装配和 `coverage_shortfall` / `deterministic_fill` telemetry，平台 style mismatch 改为软分排序而非硬拒绝。
- **Inspiration 轴覆盖装配纯函数**：新增 `materialize_platform_keywords()`、`MaterializeCandidate`、`AllocationTarget` 与 `platform_style_score()`，将候选装配改为按 interest × axis × platform 覆盖优先、软分排序，并在薄池 / 单轴 / 脚本不匹配时输出 `deterministic_fill` 与 `coverage_shortfall` telemetry；inspiration 消费路径不再把平台 style mismatch 当硬拒绝。
- **Inspiration 二级兴趣抽中即降权**：新增 `discovery_interest_selection_ledger` 与 `record_keyword_interest_selection()`，planner 选中 like 二级兴趣后立即记录 selection event，并把 14 天 recent `interest_selection_count` 合并进 coverage snapshot 的分母；真实运行使用 production scope，`keyword-inspiration-dry-run` 使用独立 preview scope，因此连续 dry-run 可以验证兴趣轮转但不会污染正式抽样状态。画像整理的 `migrate_keyword_interest_labels()` 同步迁移 keyword 与 selection ledger 标签，避免标签重命名后冷却计数孤儿化；`keyword-inspiration-report` 输出 production / preview 抽中分布，selection ledger 写入时会清理 30 天前记录。
- **Sampling / Grounding Budget / Enablement Gate 落地**：inspiration flow 现在只从 like 二级兴趣抽样，先用 coverage-aware 大窗口排序，再由 `inspiration_interest_sample_size` 控制本轮样本；regular + explore 同轮触发时共享 selected interests、deterministic probes、grounding evidence 和一次 axis-keyword LLM 输出，搜索预算由 `inspiration_max_probe_searches_per_stage`、`inspiration_platforms_per_probe` 和 `inspiration_riskcontrolled_probe_budget` 约束；coverage join 统一归一化，画像整理会迁移 `discovery_keywords.source_interest`；新增 `get_keyword_cohort_stats()` 与 `keyword-inspiration-report`，按 inspiration / merged cohort 对比准入、delight 和 topic 多样性，作为开启 replace 的机械门禁。
- **Inspiration 产词多样性补强**：planner 先复用 `discovery_inspiration_axis` 里的既有轴和 `example_terms`，再用 fresh grounding evidence 让单次 `discovery.keyword_inspiration` 返回新轴和关键词；LLM 失败或输出不足时用轴库候选与 `deterministic_fill` 补齐覆盖，避免某个已选二级兴趣整轮失声。写库前保留 provenance metadata，平台检索语法只参与软分排序，未知英文 fallback 不再吐 `community recommendations` 这类泛词占位。
- **Inspiration grounding 真实请求诊断加固**：平台源结果低覆盖或有 timeout / budget skip 时，fallback provider 会继续补 Exa / You.com，而不是因为单一平台有结果就提前停止；`grounding_ledger` 现在记录 fallback provider 的 success / failure / empty / augmentation 计数，能直接看出外部搜索是成功、空结果、限流还是超时。mcporter 远端 provider 默认 timeout 收窄到 6s，短于 planner 外层 8s timeout，避免外层取消导致 failure 不入账。
- **X 接入 inspiration 平台源**：`platform_sources` 现在会在 `[sources.twitter].enabled=true` 时复用现有 `XClient.search()` / x.com cookie replay，把推文标题、URL、作者、长文正文和互动指标作为 inspiration-only evidence；X 与 B 站 / 抖音 direct 一样计入 risk-controlled grounding 预算，失败或限流时继续 fallback 到 Exa / You.com，不写候选池。
- **Inspiration 多屏和平台源扩展**：新增 `[discovery].inspiration_search_pages_per_probe`（默认 `1`，合法范围 `1..5`），让 grounding 可在支持分页的平台源里翻多页，并把 Exa / You 等一次性搜索的请求量按页数扩展；`platform_sources` backend 补齐抖音 direct-client、可注入小红书 bridge、可注入知乎 bridge 的 inspiration-only 映射，所有结果只作为标题 / URL / 摘要 evidence，不写 `discovery_candidates`。
- **Local-first inspiration grounding**：`[discovery].inspiration_search_backends` 默认改为 `["local_cache", "platform_sources", "exa", "you"]`，先复用本地 `content_cache` 作为灵感 evidence；本地命中不消耗外部 grounding 预算，也不会继续触发 fallback augmentation。`discovery_keywords` 新增 `grounding_source` 溯源字段，dry-run / cohort report 可看到 local hits、saved searches 和本地 evidence mix。
- **Discovery query 纯规则基础设施**：新增 `discovery.inspiration` 纯规则模块和 mcporter search provider，可从搜索预览中抽取具体相邻概念、过滤空噪声 / 重复项，并把二级兴趣、轴库和 pooled terms 转成确定性 grounding probes。
- **KeywordPlanner 可选 inspiration stage**：新增 `[discovery].inspiration_search_enabled=false` 及窗口 / 上限配置。开启后 planner 从 like 二级兴趣选择、轴库和搜索 evidence 触发单次 `discovery.keyword_inspiration`，额外插入带 `source_interest/axis_label/inspiration_id` metadata 的关键词；默认关闭以避免默认增加搜索 / LLM 成本。
- **Inspiration-only 实验模式**：新增 `[discovery].inspiration_replace_merged_keywords=false`。开启后且 search provider 可用时，due 平台会跳过旧 `discovery.keyword_planner` merged call，只通过 search-backed inspiration flow 产词；当 B 站 explore 到期且有补货空间时，同轮额外用 `query_kind="explore"` 写入 `keyword_kind="explore"` 的探索词池，便于真实比较“全新流程”对关键词具体度和丰富度的影响。
- **平台专属 inspiration 关键词**：`discovery.keyword_inspiration` 输出 schema 现在直接要求每条 keyword 带 `platform`、`interest`、`axis_id_or_label` 和 `core_concept`，避免同一批搜索灵感扩展词被横向复用到所有平台。
- **Inspiration 继承平台供给优势**：旧 merged keyword planner 的静态 `<supply_advantage>` 抽成共享平台 guide，`discovery.keyword_inspiration` 现在会收到每个平台的 `supply_advantage`、`recent_keywords`、`avoid_*`、`prefer_axes`、`cold_start` 和 data-driven `supply_hint`，避免 inspiration-only 替换模式只靠 LLM 常识生成平台化关键词。
- **Like 二级兴趣选择流程落地**：inspiration stage 现在先从 like/accepted/profile-backed 二级兴趣构建 coverage-aware 抽样窗口，再结合 axis saturation 生成确定性 probe；dislike 只作为 avoid/boundary，不作为正向 seed。
- **Inspiration 兴趣窗口去粗粒度坍缩**：二级兴趣选择器现在优先读取 `OnionProfile.interest.likes[].specifics`，只有某个一级兴趣没有有效 specifics 时才把一级 domain 当作低特异性兜底；窗口选择阶段按 parent 计数降权，避免小窗口被同一个一级领域连续占满。coverage 评分也纳入 raw candidate 数量 / 占比和 dominant candidate content type share，让“已经探出很多候选”的兴趣更快冷却。
- **Inspiration prompt 成本收敛**：完整 coverage snapshot 仍在本地用于抽样和冷却；传给 `discovery.keyword_inspiration` 的 payload 只保留已选兴趣、既有轴、fresh evidence、platform guides 和 allocation targets，并对兴趣、轴、证据和平台 guide 做上限裁剪，避免真实库里几百个历史 `source_interest` 把 LLM prompt 放大到十万 token 级别。
- **Inspiration 搜索后端链**：新增 `[discovery].inspiration_search_backends=["local_cache", "platform_sources", "exa", "you"]`，runtime 和 `keyword-inspiration-dry-run` 会先复用本地 `content_cache`，本地证据不足时再从用户已启用的同步 / bridge 平台源抽样做 inspiration-only grounding，不写候选池；平台源为空或失败时继续尝试 Exa 和 You.com Free MCP，降低真实 dry-run 被单一免费额度卡死的概率。
- **Coverage snapshot 反复搜索降权**：新增 `Database.get_keyword_interest_coverage_snapshot()`，按 `discovery_keywords.source_interest` 汇总 generated/selected/yield 计数，并结合 `discovery_candidates` raw candidate 分布和 `content_cache.topic_group/pool_topic_label` admitted share，让“生成过很多词”“raw 候选占比已经很高”或“最终入池占比已经很高”的二级兴趣下轮抽样概率降低。
- **Inspiration deterministic guardrails**：写入关键词池前由 `materialize_platform_keywords()` 强制 interest × axis × platform 覆盖优先、每平台上限、脚本匹配和去重；候选不足时用 axis `example_terms` 做确定性补位，并把无法补齐的槽位记录到 `coverage_shortfall`。
- **Inspiration 真实请求质量加固**：单次 `discovery.keyword_inspiration` 输出会经过 tolerant JSON object 解析，支持截断 salvage 已完整的 `axes[]` / `keywords[]`；`platform_guides` 继续携带 `query_style`，让 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit 分别按视频、笔记、短视频、英文视频、话题热议、问答和社区讨论语法产词；平台 style mismatch 改为 `platform_style_score()` 软排序，不再进入硬拒绝原因。
- **Inspiration preview CLI**：新增 `openbiliclaw keyword-inspiration-dry-run` / `keyword-inspiration-preview`，可真实调用当前 discovery LLM + 搜索 provider 链，输出 selected interests、deterministic probes、grounding records、平台关键词、materialize telemetry 和 rejected reasons，但不写入 `discovery_keywords`；`--persist-axes` 可只写回 axis 库，用于调试搜索词丰富度和轴库复用。
- **Discovery keyword caller 路由修正**：`discovery.keyword_planner` / `discovery.keyword_inspiration` 归入 `[llm.discovery]` bucket，真实运行会使用 discovery 模块配置的模型；axis-keyword JSON 解析复用 tolerant helper，可接受 code fence / 外层说明，并优先解析 `{axes[], keywords[]}`。
- **搜索预览噪声过滤**：inspiration seed 抽取会过滤 Markdown 表格分隔符、纯标点和短残句，避免 `| --- | --- |`、`故事是` 这类搜索预览噪声进入关键词池。
- **灵感、轴库与横向扩展持久化**：SQLite 初始化新增 `discovery_inspiration_probe_cache`、`discovery_inspiration_expansion_cache` 与 `discovery_inspiration_axis`，并提供 probe / expansion yield DAO 以及 `upsert_inspiration_axes()` / `list_inspiration_axes()`；同一灵感、扩展或轴可刷新证据字段，但不会清零反馈计数。
- **关键词溯源元数据与 yield 回填**：`discovery_keywords` 增加 `aspect_id`、`inspiration_id`、`expansion_id`、`angle_id`、`source_interest`、`generation_reason` 等可选字段；`insert_pending_keywords()` 支持 `metadata_by_keyword`，但去重键仍保持原来的 `(platform, keyword, profile_kw_digest, keyword_kind)`。`increment_keyword_yield()` 成功记录新内容 yield 后，会把计数回填到对应 inspiration / expansion，重复 content 不会 double-count。
- **跨设备扩展认证与密钥管理（PR #99）**：远程扩展访问默认关闭；CLI 生成高熵设备密钥，配置仅保存 SHA-256 摘要，扩展用其换取最长 168 小时的短会话。
- **最小凭证暴露面**：普通 HTTP 统一使用 `Authorization: Bearer`，仅 WebSocket 与图片代理因浏览器接口限制携带短会话 query；长期设备密钥不进入普通请求、URL 或日志。
- **设备生命周期 CLI**：`ext-key generate/enable/disable/list/revoke` 管理密钥；撤销会提升 `auth_epoch` 立即失效全部现有会话，运行库失败时配置自动回滚。
- **最小远程 host 权限**：扩展保存 LAN / 远程 endpoint 前请求 `scheme://host/*` 可选权限；权限 API 无法跨浏览器限定端口，实际请求仍固定到配置端口。公网地址强制 HTTPS，WebSocket 自动使用 WSS。
- **兼容升级**：清理 PR 早期的密码缓存与裸 token 存储，不采用 Extension ID / RSA manifest key 或 Docker 网关可信绕过。

## v0.3.160 / extension v0.3.160 / desktop v0.3.160：把 bge-m3 打进交付物,消灭装机时的模型下载（2026-07-07）

后端源码走 `backend-v0.3.160`,桌面安装包走 `desktop-v0.3.160`(浏览器插件本版无代码改动,不单独发 `extension-v*`)。设计见 `docs/plans/2026-07-07-bundled-embedding-model-{spec,plan}.md`(经 codex 3 轮对抗 review 收敛)。

- **Discovery 除 `explore` 外不再给任何来源 / 策略评分开后门（修复 #90）**：prompt 与代码两层同时收口。单条与 batch 内容评估 prompt 删除 `trending 基础分 >= 0.6`、`search` 特判和 `related_chain` 放宽语义；热门、推荐流、搜索命中、相关推荐、订阅频道和平台算法背书现在都只作来源上下文，不能设置基础分、自动加分、降低门槛或替不匹配内容事后编造画像关联。代码侧同步堵掉三条绕过路径：`RelatedChainStrategy` 删除 LLM 打分后叠加的 seed / depth bonus（最高 +0.05，足以把 LLM 只给 0.55 的候选顶过 0.60 admission 门槛）;`PoolCurator._serendipity_bonus` 不再给 `trending` 固定 0.5 novelty 分（按默认 serendipity 权重折算相当于每条白拿 +0.10 rec_score），`explore` 仍保留满额;`DiscoveryCandidatePipeline._threshold_for()` 现在把非 `explore` 候选自带的 `score_threshold`（行字段或 `raw_payload`）钳到全局 `admission_min_score`,任何来源只能抬高自己的门槛、不能压低。明显不匹配画像的 YouTube 匿名热门等候选因此可以正常低于 admission 门槛并被拒绝;只有 `explore` 继续允许主题陌生和略低阈值,但仍须有具体可信的吸引点。三条路径均补了行为级回归测试（已在临时 worktree 里验证:修复前全红、修复后全绿）。
- **#90 永久防线补齐到缓存与展示出口**：新增依赖轻量的统一 admission policy，精确 `explore` 是唯一 `0.58` 例外，其他来源至少使用全局门槛；`ContentDiscoveryEngine._cache_results()` 写库前再次 fail closed，`Database.cache_content()` 缺失分数改存 `0.0`，未来新增直接写入者不能凭空获得合格分。普通取池、缓存回填、平台补位、文案预计算、配额统计、历史推荐与 delight 出口全部复用来源感知门槛，让 `explore=0.58` 真正可展示、非 explore 同分仍不可见。OpenClaw 兼容启动路径也构造并共享 `DiscoveryCandidatePipeline` 给 refresh controller、抖音与 YouTube producer，不再保留 runtime 编排旁路。
- **Docker:bge-m3 烤进镜像,容器零 pull 离线就绪**。新增 `openbiliclaw-ollama` 内置模型镜像(`docker/ollama-bundled.Dockerfile`:构建时 `ollama pull bge-m3` 并校验 digest 对齐 allowlist,快照到 `/opt/bge-m3-seed/` 避开命名卷遮盖)+ 独立 shell seeder(`docker/seed-bge-m3.sh`,`sha256sum` 逐 blob 校验、manifest 最后写作提交标记)。entrypoint 启动时缺模型才播种,**播种失败明确报 unhealthy 不静默降级**,网络补拉改 `OPENBILICLAW_OLLAMA_ALLOW_PULL=1` 显式 opt-in。两个 compose 换用该镜像;`release-docker.yml` 多架构构建/推送 backend + ollama 两镜像,聚合页 docker 就绪需两镜像都可拉。
- **桌面:发 lean / with-embedding 两个安装包,Release 可选**。`with-embedding` 变体把 bge-m3 预置进包(`build.py --bundle-embedding` / `OPENBILICLAW_BUNDLE_EMBEDDING=1`,`packaging/make_model_seed.py` 制种),首启在任何 ollama 启动前**自起私有 Ollama**(独立端口 `127.0.0.1:11435` + 用户可写纯 ASCII 模型目录),把权重播种进去再 serve,embedding base_url 指向私有端点——彻底绕开外部/官方 Ollama 的 store 竞态与中文用户名路径 bug。lean 变体行为逐字不变(无 seed 目录时全部 no-op)。Windows 变体名由 Inno `MyAppVariantSuffix` 区分,mac 名追加 `-with-embedding`;CI 双变体矩阵 + 每资产 < 2GB 硬门;聚合页 prune 改为**只删被同名替换的资产**,某变体构建失败不误删上一版完整包。
- **播种核心**(`runtime/embedding_seed.py`):共享 blobs 目录上逐 blob 临时→sha256 校验→原子 rename,manifest 最后提交,目录锁,幂等,绝不动其它模型 blob,任何完整性失败即回落网络下载;`effective_embedding_models_dir` 选 ASCII+用户可写目录(合法 `OLLAMA_MODELS` > `%PROGRAMDATA%` > `/var/tmp/openbiliclaw-<uid>`)。
- **真机验证**:用真实 1.08GB bge-m3 制种→播种→私有端口 ollama 零 pull 识别 `bge-m3:latest` 并产出真实 1024 维 embedding;后端 `serve-api` 读改写后配置,`/api/health` `embedding_ready=true` 走真实 embedding 请求打私有 daemon;21 单测 + mypy/ruff 全绿。
- **真 CI 发布验证(本版)**:`release-docker` 多架构成功推 GHCR `openbiliclaw-backend:0.3.160` + `openbiliclaw-ollama:0.3.160`(bge-m3 烤入,Dockerfile build-time pull + digest 校验通过);`release-desktop` 四变体全绿并发到 `desktop-v0.3.160`——`OpenBiliClaw-macos-v0.3.160-arm64.dmg`(205MiB)/ `-arm64-with-embedding.dmg`(1210MiB)/ `OpenBiliClaw-windows-0.3.160-Setup.exe`(64MiB)/ `-with-embedding-Setup.exe`(1032MiB),均 < 2GB 门;聚合页 `openbiliclaw-v0.3.160` 列齐四资产 + 双镜像。首轮 CI 抓到并修复一个 Windows-only 失败(pwsh `$flags=""` 空参数被 argparse 拒收,lean 变体挂在 PyInstaller 步;改参数数组修复)。
- 更正过期文案:全仓库 bge-m3 体积 `~568MB` → 实测 `~1.1GB`。

## v0.3.159 / extension v0.3.159 / desktop v0.3.159：自动更新被拒时暴露真实远端地址（2026-07-07）

后端源码走 `backend-v0.3.159`，浏览器插件走 `extension-v0.3.159`，桌面安装包走 `desktop-v0.3.159`。

- **惊喜推荐动态阈值不再在初始化后误飙高**：默认 delight 底线从 `0.70` 提到 `0.75`，并把动态 Top 10% 阈值的启用条件收紧为至少 150 条已打 `delight_score` 且分布标准差 ≥ `0.08`；初始化后那种小样本、同质高分池会继续使用底线，避免首开 App 时惊喜推荐被动态阈值清空。动态边界现在也按实际 gate 的 `delight_score` 计算，不再用 `relevance_score` 代替。
- **兴趣/避雷探针新增「暂时忽略」（defer/搁置）状态**（产品思路来自社区 PR #82 @15515151，重做为真正的持久化状态而非纯审计日志）：此前探针只有「喜欢/不喜欢/多聊聊」，用户「暂时无感」时只能选「不喜欢」（30 天冷却+降权，过重）或不理（探针一直占消息位）。现新增**「暂时忽略」按钮**（位于喜欢与不喜欢之间，中性灰）：点击后探针置为 `deferred` 并按阶梯隐藏——第 1 次 7 天、第 2 次 14 天，第 3 次耗尽转 30 天 cooldown（走 TTL 过期语义、记 `defer_exhausted`、**不**进 handled 集，冷却后可重新猜；区别于显式「不喜欢」的永久拉黑）。`deferred` 探针从所有读侧（pending 端点 / WS 推送 / `get_active_speculations`）消失，刷新页面不再弹回；`tick/force_tick` 维护段末尾的 `revive_deferred` 在到期后把它复活为 `active`（重置 TTL 窗口、`confirmation_count` 夹到阈值-1，保证复活后先以探针再露面而非静默转正）。避雷探针有对称实现，复活排在 compaction 之后避免同轮被压缩。聊天说「先放着」「稍后再看」也会经新的 `neutral_deferred` 分类走同一条搁置路径（态度模糊的「再看看」仍是普通 neutral，不改状态）；探针情绪分类的系统提示迁入 `llm/prompts.py:build_probe_sentiment_prompt` 静态常量以命中 prompt 缓存。桌面 Web 三处入口（消息卡 / Profile 猜测列表）与移动 Web 均已适配，文案诚实标注「过阵子可能再提」而非「已忽略」。`deferred` 不改画像，故不触发 profile 刷新。新增 speculator / avoidance / API / 前端契约多层回归测试。
- **自动更新 `untrusted_remote` 被拒时把真实远端地址带到 UI**：用户群实测——git 安装点「立即应用」被「git 远端不在允许列表」拦下，但状态卡的「最近错误」只有这句泛化文案，用户无从知道自己的 origin 到底是什么、该怎么改，只能去翻后端日志。根因是 v0.3.152 承诺「每条拒绝把实际 remote URL 写入 `last_error` 供状态卡展示」但实现只把裸 reason 码 `untrusted_remote` 塞进了 `last_error`（`AutoUpdateService._apply` 覆盖成 `guard_reason`），前端再把该码映射回同一句泛化文案，真实地址永远只进日志、进不了 UI。修复：`_check_apply_guards` 三条拒绝路径（无 origin / 内嵌凭证 / 不在允许列表）各生成含**实际远端地址 + 一键修复命令**的中文明细存入新增的 `_guard_detail`，apply 优先用它写 `last_error`（无明细的守卫仍回落裸码经 i18n 映射）；新增 `_redact_remote_url` 确保 `https://<token>@github.com/...` 这类内嵌凭证不泄露到 UI。桌面 Web 的 `blocked` 分支此前完全没读 `last_error`，补上（与 `error` 分支对齐）；插件 popup 依赖既有「非 reason 码则原样显示」的回退，后端带明细即自动展示，无需改动。**这不是自动解封**——被拒的 git 安装用户（origin 是镜像 / 带 token / fork）修完后能在「最近错误」直接看到自己实际的 origin 与 `git remote set-url origin …` 修复命令自助解决；安装包（frozen）用户走 `unsupported_install_mode` 检查-提醒-下载新包路径，不受影响。新增守卫明细含 URL + 修复命令、内嵌凭证脱敏两条回归测试。
- **小红书 / 知乎 / Reddit 扩展后端来源接入状态改用真实登录信号**：插件 `cookie-sync` 现在监控 `xiaohongshu.com` 的 `web_session` 和 `zhihu.com` 的 `z_c0`，只向 `/api/sources/xhs/login-state` / `/api/sources/zhihu/login-state` 上报 `logged_in` 布尔值（不上传 cookie 值；`a1` / `webId`、`_xsrf` / `d_c0` 等游客设备 cookie 不算登录）；后端把状态与时间戳存入 `auth_state`，`/api/sources/status` 在 72 小时窗口内以登录态决定小红书 / 知乎 `ready`，缺失或过期时再回落到既有任务历史逻辑。Reddit 的 extension/plugin 后端则先看已同步到 rdt credential store 的 `reddit_session`，有凭据即显示已登录，缺凭据时才保留任务历史的 `unverified` / `missing` / `partial` 判定。新增 DB helper、API 状态分支和扩展 cookie-sync 三层回归测试。
- **插件消息卡按钮不再「有时候没反应」**：用户群实锤——插件 popup 消息面板里兴趣/避雷确认卡的「确实不喜欢 / 不是 / 多聊聊 / ×」按钮有时点了没反应。根因是 `renderMessagesList()` 每次都 `container.replaceChildren()` 整列重建，而按钮的 click 监听绑在**单个按钮节点**上——聊天轮询（有卡片在「多聊聊」pending 时每 ~1.2s 一次）、`openMessagesPanel` 打开后 `loadProfileSummary` 完成的第二次重渲染、以及别的卡响应，都会把用户正要点的按钮换成新节点，点击落空（网络快慢决定了「有时候」）。桌面 Web 早有 `messageListDomLocked` 交互锁，popup 一直裸重建。改为**在永不被替换的消息容器上做一次事件委托**（按 `data-msg-action` + 卡片 `dataset` 派发），免疫任意次子节点重建；顺带加 disabled（pending chat）拦截与 in-flight 防抖双击守卫。新增 popup 静态契约测试锁住委托接线。
- **桌面 Web 惊喜卡「去看看」按钮真的能打开内容了**：用户群实锤——点桌面 Web 惊喜推荐卡上最显眼的「去看看」CTA，弹出「已打开惊喜推荐」toast 却什么都没打开，只有点封面缩略图才真打开。根因是 issue #75「real card links」把封面 `#delightThumb` 改成带 `href` 的 `<a>`（靠原生导航打开），但「去看看」还是纯 `<button>`，其 `respondDelight("view")` 分支只上报点击 + 弹 toast、漏了 `window.open`。修复：`respondDelight` 加 `openUrl` 参数，「去看看」按钮（`[data-delight="view"]`）点击时显式 `window.open(url)`（在点击手势同步栈内，不被拦截）；封面 `<a>` 仍走原生导航、`openUrl=false` 不重复开，避免双开。插件 popup（`chrome.tabs.create` / `window.open`）与移动 Web（`openContentUrl`）本就正常打开，不受影响。真实浏览器 E2E 验证点「去看看」真的开出带正确 URL 的新标签页；新增桌面契约回归测试。
- **桌面 Web 惊喜推荐卡也显示播放/点赞/评论数据**：用户群反馈「有的卡显示 ▶6.9万，有的不显示，能都显示点赞评论吗」。根因是 issue #75 的卡片元数据（▶播放/👍点赞/💬评论/⭐收藏/弹幕）只接了「为你推荐」网格卡，**惊喜推荐 hero 卡从来没渲染统计行**，而且后端 delight 数据（`PendingDelightOut` / pending-batch / `delight.candidate` 事件）压根没带这些计数。修复：后端三处 delight 出口都从 content_cache 带出 `view_count/like_count/comment_count/danmaku_count/favorite_count`，前端 `normalizeDelight` 接收、`setActiveDelight` 用与网格卡共享的 `recommendationStats` 填充新增的 `#delightStats` 行（无计数时隐藏）。B站/X/知乎/Reddit 的惊喜卡立刻能显示点赞评论；YouTube/抖音/小红书因发现路径本就没抓这些统计仍空缺（是另一档「补平台数据」的活）；移动 Web 目前整体未接卡片统计（issue #75 为桌面专属），不在本次范围。真实浏览器 E2E 验证惊喜卡显示「▶ 6.9万 · 👍 3200 · 💬 880 · 弹幕 150」；新增后端 pending-batch 统计透传与前端 delight 统计渲染回归测试。
- **三端(桌面 / 移动 Web / 插件 popup)统一显示浏览/点赞/评论/收藏 + 小红书收藏补齐**：延续上一条,把互动统计行铺到全部三端。(1) **小红书收藏 collect→⭐**:小红书把「收藏」存在 `collect_count`,而卡片 ⭐ 渲染的是 `favorite_count`(B站用这列,小红书为 0),导致小红书收藏数不显示;后端 5 处序列化(推荐 + 惊喜)统一 `favorite_count or collect_count` 兜底,小红书收藏立刻能显示。(2) **移动 Web 与插件 popup 补齐整个统计行**:此前这两端**完全没渲染统计**(issue #75 是桌面专属),现在照抄桌面 `formatCountCn`/`recommendationStats`(▶播放 · 👍点赞 · 💬评论 · ⭐收藏 · 弹幕,万/亿 格式、有数才显示)给两端的推荐卡 + 惊喜卡都加上;移动的 `normalizeRecommendation`/`normalizeDelightCandidate` 与 popup 的 `normalizeRecommendation`/`normalizeDelightCandidate` 此前都丢弃统计字段,已一并穿透。至此 B站/YouTube/X/抖音/知乎/Reddit/小红书 的浏览/点赞/评论/收藏在三端一致展示(知乎/Reddit 天生无「播放」故只显点赞评论)。真实浏览器 E2E 三端各验一遍(桌面惊喜卡、移动惊喜卡、popup 推荐卡均正确渲染 ▶/👍/💬/⭐)；新增后端 collect→favorite、移动与 popup 静态契约及 normalizer 穿透回归测试。
- **初始化失败时页面端展示具体的 LLM 错误原因**：用户群日志实锤——引导初始化在画像阶段被 LLM 打挂时(如 compat 网关因内容审查把拒答当 `500` 抛出、或主/备 Provider 全部不可用「No provider was available」),页面端要么只显示笼统的「初始化过程中出错了,请稍后重试」,要么把生 traceback 片段 `InternalServerError: 非常抱歉,根据相关法律法规…` 原样截断塞进去,用户既看不懂也不知怎么办。根因是真实的 LLM 失败原因在到达 `init-status` 的 `detail` 字段前被丢弃:`cli.py` 画像阶段 catch 后只 `raise GuidedInitError("profile_failed", "画像生成阶段出错")` 吞掉了 `__cause__`,而 `_init_crash_detail` 只做「类名 + 首行」摘要。新增 `llm/base.py:describe_llm_failure(exc)` 分类器——走 cause 链把 LLM 异常翻成可操作的中文短语:内容合规拒答(嗅 `法律法规`/`10013` 等标记,建议换不带审查的模型)、主备 Provider 全挂(检查配置密钥网络)、限流、超时、空响应,非 LLM 错误返回 `None` 不误判。画像阶段与崩溃摘要两处失败路径都接上该分类器,前端三端(桌面 / setup / popup)本就把 `detail` 拼进失败文案,故零改动即生效。新增分类器各分支 + 崩溃摘要改写回归测试。(注:开始前的预检 `llm_not_ready` 仍是静态文案,补充 per-prereq LLM 明细是后续项。)

## v0.3.158 / extension v0.3.158 / desktop v0.3.158：向量模型部署自愈完备、首启拉取进度可见、MiniMax-M3（2026-07-06）

后端源码走 `backend-v0.3.158`，浏览器插件走 `extension-v0.3.158`，桌面安装包走 `desktop-v0.3.158`。

- **首启 bge-m3 下载进度可见 + Ollama 未运行自愈**：桌面包首启后台自动拉取 `bge-m3`（约 568MB）不再只在控制台输出，新增进程全局 `runtime.embedding_progress`，让 `/setup/` 与 `/web` 的 guided-init checklist 共享显示自动拉取和手动修复的进度条、百分比文案与「Ollama 启动中…」阶段提示。`/api/embedding/repair` 遇到 `not_running` 时会在 `autostart.manage_ollama=true` 且 endpoint 是默认 loopback `localhost:11434` 时先尝试拉起托管 Ollama，成功后重新诊断并继续既有 ok / 拉取 / 路径迁移流程；远端、自定义端口或外部 Ollama 仍保持 409，不越权接管。
- **向量模型一键修复补齐硬失败分流**：`/api/embedding/repair` 现在是有界的「诊断 → 自动动作 → 重新诊断」编排器，避免用户陷入重复点击重试。新增 `disk_full` / `network` / `model_oom` 诊断：磁盘不足会在拉取前按 Ollama 模型目录所在卷预检并直接提示清理 / 迁移；下载源网络 / 代理 / TLS 问题不再被误判为本地模型损坏；内存不足从 `model_broken` 中拆出并明确重拉无效。泛 `provider_error` 只对本进程托管的默认 loopback Ollama 尝试一次重启，仍失败则给升级 Ollama、检查 11434 端口和 `NO_PROXY` 的排查提示。
- **新增 MiniMax-M3 模型（默认推荐模型）** —— `MiniMax-M3`（5-2026 / 1M 上下文 / 图文视频输入）加入内置 provider 选单并设为 MiniMax 的默认模型，定价 $0.60/$2.40 per M，适合推荐这类结构化输出任务；同步更新 provider 选择提示与计费表。

## v0.3.157 / extension v0.3.157 / desktop v0.3.157: 惊喜推送不打断输入、备选 Provider 容灾完备、桌面 Web 体验打磨（2026-07-05）

后端源码走 `backend-v0.3.157`，浏览器插件走 `extension-v0.3.157`，桌面安装包走 `desktop-v0.3.157`。

- **Windows 中文用户名导致向量模型加载失败可自愈**：用户截图实锤——`bge-m3` 已下载但 `llama-server` 以 `failed to load model from C:\Users\<乱码>\.ollama\models\...` 退出，根因是模型路径含非 ASCII 字符（常见于中文 Windows 用户名），重新下载无法解决。新增诊断码 `model_path_encoding` 精确区分此类失败（命中「failed to load model / llama_model_loader」且路径含乱码或非 ASCII 用户名，不误伤真·下载损坏与内存不足）；托管 Ollama（本应用亲手拉起）时「自动下载向量模型」按钮升级为「迁移模型目录并修复」，把模型目录迁到纯 ASCII 的 `%PROGRAMDATA%\OpenBiliClaw\ollama-models` 并带 `OLLAMA_MODELS` 重启后重拉（目录存在即持久迁移标记，`setdefault` 保证用户显式 env 优先）；检测到外部启动的 Ollama 则拒绝越权重启、返回明确的手动设置 `OLLAMA_MODELS` 指引。三端修复按钮文案与 409 失败详情同步。
- **桌面 Web 交互打磨（issue #75）**：视频卡片改真链接（中键 / Ctrl+点击可用）+ 时长 / 播放 / 点赞 / 弹幕元信息 + UP 主跳转；新增暗色模式（跟随系统 / 手动）；抽屉退出动画、分区切换过渡、滚动条防抖动；滚动到底自动加载（带候选池保护）；画像编辑即时反馈。
- **插件配置页预算说明补齐（与桌面 Web 同文案）**：桌面 Web 设置页已给每源预算行补的中文说明，插件 popup 的同批 `daily_*_budget` 数字框此前只有 `placeholder="0 = 不限"`、没有解释，用户仍会把「填 1」误当成打开该源。现在六个源（小红书 / 抖音 / YouTube / X / 知乎 / Reddit）的预算组各补一行与桌面 Web 逐字一致的 hint（Reddit 变体附「各分支默认 300」）。新增 `popup-settings.test.ts` 静态契约断言每个预算组都带该说明。
- **X 限流冷却改为递增（30m→2h→6h，成功即复位）**：X discovery 是服务端 cookie 回放，命中 HTTP 429 时健康存储此前固定冷却 30 分钟，持续被限流的账号会每 30 分钟无意义地再戳一次 x.com。现在 `XSourceHealthStore` 按连续 429 次数递增冷却——第 1 次 30 分钟、第 2 次 4×（2 小时）、第 3 次及以后 12×（6 小时封顶，以构造器 `rate_limit_cooldown_minutes` 为基准步长派生），升级时打一行 WARNING；任一次成功抓取即把梯子复位到基准步长。新增 `consecutive_rate_limits` 列（存量库自动迁移）。新增连续 429 升级 / 成功复位 / 封顶 / `is_ready()` 遵守放大窗口的回归测试。
- **主/备选 Provider 机制审查后的三处行为修正**：对主 + 备选 Provider 全链路做正确性审查后落地：(1) **空响应也回退**——劣质中转网关最常见的死法是 HTTP 200 但内容为空（`LLMResponseError`），此前该类错误直接上抛、配好的备选一次都不会接管，现在与 5xx / 超时 / 限流一样换备选重试（provider 内部原有的单次自重试保留；单 provider 链耗尽统一抛 `LLMFallbackError`，原始错误在 `__cause__`）；(2) **init 前置检查认备选**——`chat_ready()` 此前只探主 Provider，主挂、备选健康时初始化被「AI 服务不可用」拦住，与 fallback 的容灾定位自相矛盾，现在主探测失败且存在可用备选（已注册、chat-capable、非同名）时补探备选，任一通过即就绪（经备选通过时 INFO 说明）；(3) **移除从未生效的 `[llm].fallback_enabled` 布尔开关**——该字段被 UI 读写、落盘、回显，但回退链从未读取（手写 `false` 并不能关闭 fallback），语义收敛为「`fallback_provider` 非空即启用」：config 加载忽略存量 key、PUT 忽略旧客户端仍发送的该字段、GET 不再回显（embedding 侧的同名字段仍有效）。顺带修正单 provider 链失败时误导的「trying next fallback」日志（无备选时改为明确的 no-fallback-left 文案）。新增空响应回退 / 单链耗尽 cause 链 / chat_ready 四场景（备选接管、双挂、同名跳过、主健康不多探）回归测试。
- **插件 popup 备选 Provider 保护对齐桌面 Web**：side panel 设置页的备选 LLM 下拉此前允许选成与默认 Provider 同名（后端会以 400 拦截保存，但 UI 无事前提示），且没有备选连通性测试入口。现在与桌面 Web 一致：备选下拉禁用与默认 Provider 同名的选项（切换默认 Provider 时动态更新）、同名旧配置水合时显示行内红色警告而不静默改数据、「测试 LLM」旁新增「测试备选 Provider」按钮按当前表单草稿走 `/api/config/probe-service` 的 `kind="llm_fallback"` 精确探测备选（未配置 / 同名时按后端明确拒因行内展示）。新增 popup 静态契约测试（同名守卫 + 探测按钮接线）。
- **内容口味画像不再因 LLM 违规输出全成 unknown / 0%**：用户截图实锤——「内容口味」面板整片显示 时长 / 节奏偏好 `unknown`、深度 / 画质 / 幽默偏好 `0%`、使用场景 `未知`、探索开放度 `0%`，且日志无任何相关行，疑似「静默出错」。根因是偏好归一化对 LLM 输出零校验：`preference_analyzer.py` 两处 `style.update(<raw>)` 把不合法枚举（`preferred_duration` 合法值 short/medium/long、`preferred_pace` 合法值 fast/moderate/slow）与非数值口味字段原样落库，`exploration_openness="unknown"` 又被 `_to_float` 静默映射成 0.0（越过 0.5 默认）。三层修复：(1) **后端校验 + 纠偏 WARNING**——新增 `_normalize_style` / `_normalize_context_dict` / `_finalize_taste`，枚举越界重置为 ""、非数值口味 / openness 回落字段默认 0.5（合法的字面 0 保留不改）、数值 clamp 到 [0,1]、context 占位符（unknown/none/n/a/未知）清空，任一字段被纠偏即打一行列出字段名的 WARNING（给用户日志线索）；两处装配路径统一走该校验；(2) **三端 UI 兜底**——插件 popup（`renderStylePreference`/`renderContextMode`）、桌面 Web（`styleHtml`/`contextHtml`）、移动 Web（`view-models.js`）把 ""/unknown/none/n/a/未知 视为缺失，跳过该行、全空时回落「还在摸索 / 观察中」文案；(3) **prompt 枚举约束**——偏好分析 system prompt 明确 style 枚举取值与 0-1 数值范围、证据不足时省略字段或填 0.5、严禁用 unknown/0 当占位。新增归一化校验单测（枚举越界 + caplog 纠偏、数值 clamp、字面 0 保留、openness / context 占位符、干净载荷零 WARNING）。
- **修复 Termux/Android 上 CLI 启动即崩（issue #80）**：Gemini SDK 的可选依赖守卫此前只捕获 `ModuleNotFoundError`，而 ZeroTermux 用户的 `google-genai` 安装成功、其传递依赖 `cryptography` 的 manylinux 原生轮子在 Android Bionic linker 下 dlopen 失败，抛的是普通 `ImportError`（`cannot locate symbol "PyExc_Warning"`）——守卫没接住，导致即使完全没配 Gemini，`openbiliclaw` 任何命令都在 import 阶段崩溃。守卫放宽为 `ImportError`：SDK「装了但加载不了」与「没装」同样优雅降级（registry 跳过 Gemini 注册，其余 provider 正常可用），真正实例化 Gemini 时的报错附带底层 import 失败详情便于定位。新增子进程回归测试（伪造 dlopen 失败的 `google.genai`，断言 CLI 仍可导入且 SDK 判定为不可用）与报错详情单测。
- **惊喜推荐后台推送不再打断正在打字的用户**：用户群实锤——在惊喜卡的聊天框里打字时，后端每隔 `proactive_push_interval_seconds`（默认 120s）推的新候选会让桌面 Web 无条件切到最新一张卡（`setActiveDelight` 顺手 `closeDelightComposer` 收起输入框），「打着打着惊喜推荐突然变了」；更糟的是 `state.delight` 已指向新卡，用户重新展开把话发出去会把这条反馈**串到换上来的那张卡上**污染画像信号。修复：新增 `delightUserEngaged()`（composer 展开 / 输入框有焦点 / 有未发送草稿即视为互动中），互动中 `delight.candidate` 新候选只静默入队并刷新右上角计数、同卡更新只改数据引用不重渲染；`delight.refreshed` 队列刷新同样只同步数据，且当前卡即使已被后端消费也保留引用（发送必须落在用户正对着的卡上）；空闲时保持自动切到最新候选的原行为。移动 Web 同修：输入框聚焦时跳过推送触发的整块 DOM 重建（textarea 失焦 = 手机键盘收起，草稿本就实时存 state）。新增三端行为静态契约测试；桌面守卫经真实浏览器 E2E 验证（打字中新推送 / 同卡更新 / 空闲自动切换、草稿与焦点保持、计数照常更新）。
- **桌面 Web「加载更多」诚实化 + 首页挤牙膏首载修复（issue #81）**：两处让「换一批 / 加载更多」显得像坏了的问题。(1) **首页挤牙膏首载**——`GET /api/recommendations` 只在 `recommendations` 历史表为空时才 bootstrap 一次 `serve()`，一轮反馈后未处理窗口缩到 2-3 行时首屏只有 2-3 张卡（池里其实有 300+ 存量候选），读起来像「推荐挂了」。现在未处理行数低于 `_FIRST_PAGE_TOPUP_FLOOR`（10）即从池补服至多 10 条；因这是发生在 GET 上的副作用写入，非空历史的补服按 `_FIRST_PAGE_TOPUP_DEBOUNCE_SECONDS`（30s）去抖，池被过滤光时轮询客户端不会每 tick 重服（空历史的全新安装保持原 bootstrap、不去抖），init 进行中一律跳过（不越权从半建成的池服务）。(2) **加载更多骨架屏 + 诚实空批文案**——`appendMore` 点击即插 4 张骨架占位卡（`is-skeleton`，`prefers-reduced-motion` 关闭微光动画），返回后清骨架并保证 grid 不留白；短批 / 空批不再假装成功，按候选实际数量与自动加载开关给诚实文案（满批「已加载更多推荐」/ 短批「候选池暂时见底，后台正在补货」/ 空批「候选池暂时没有新内容，已请求后台补货」，重试提示据 `autoLoadOnScroll` 区分「补上后会自动加载」/「稍后可再点一次」，绝不对关掉自动加载的用户许诺自动补），且骨架占位卡不计入自动加载触发（`.video-card:not(.is-skeleton)`），上一批没吃饱且哨兵仍在视口时才在池回补后补一脚（防 AFK 挂底吃光整池）。新增 `tests/test_desktop_web_load_more.py` 静态契约 + `tests/test_api_app.py` 四条 thin-history top-up / 去抖 / 满页不补 / 空历史不去抖回归；桌面链路经真实后端 + 真实浏览器 E2E 验证（20→30 张追加渲染、骨架屏出现即清、诚实空批文案在池临时见底时真实命中）。
- **知乎卡片不再露 `answer_<id>` 裸 ID + 补齐封面（issue #79）**：部分知乎接口（`search_v3` / feeds / moments）返回的回答无内嵌问题标题，此前归一化直接回落 `answer_<id>` / `article_<id>` 当标题，卡片显示一串裸 ID。新增标题兜底 `zhihuDisplayTitle`（插件）/ `zhihu_display_title`（后端）：标题缺失或本身就是 `(answer|article|question|zhihu)_<id>` 形态时，取摘要首句（按中英文句读切分、超 40 字截断加省略号），再退到「来自知乎的回答 / 文章 / 提问」占位——各归一化路径（read_history / activity / collection / discovery）统一走它，后端 `zhihu_bootstrap_items_to_events` 与 `zhihu_discovery_items_to_contents` 双侧兜底。同时补 discovery 项封面：新增 `zhihuCoverUrl` 跨 `thumbnail` / `image_url` / `title_image` / `cover_url` / `thumbnail_info.thumbnails` 多种字段形状 best-effort 提取绝对 https URL，经 `ZhihuBootstrapItem.cover` 透传到 `DiscoveredContent.cover_url`（后端二次校验须 http(s) 前缀）。新增插件归一化单测（标题兜底各分支 + 封面多形状）与后端 helper 单测；经真实登录态浏览器 E2E 验证（`search_v3` / 我的回答 / 热榜真实响应：全量 0 个裸 ID、真实回答的 `thumbnail` 正确提取为封面、空图字段正确留空、extension→backend 封面透传闭环）。**信息密度与视觉统一（issue #79 §2/§3）续补**：知乎这类文字源没有播放 / 弹幕数，此前桌面卡片统计栏最多只剩一个点赞——`RecommendationOut` 新增 `favorite_count` / `comment_count` 字段、`_serialize_recommendation_items` 与历史行 join（`get_recommendations` 的 `SELECT` 补 `COALESCE(c.favorite_count/comment_count)` 两列，防 issue #75 式「桩测试看不见 SQL 缺列」）双路贯通，桌面卡片 `recommendationStats` 增补 `💬` 评论 / `⭐` 收藏；带封面的文字卡（如知乎回答提取到 `thumbnail`）改用毛玻璃背景（模糊封面打底 + 半透明 scrim + `backdrop-filter`），与图片卡统一视觉、弱化对封面有无的样式割裂；桌面前端 `normalizeRecommendation` 追加防御性标题兜底（存量 `answer_<id>` 缓存行按摘要首句 / 占位文案兜底，无需回捞）。新增后端 discovery→content 封面 / 标题边界单测、`get_recommendations` 计数列真库回归（issue #75 教训对症）、桌面毛玻璃 CSS 静态契约断言。

## v0.3.156 / extension v0.3.156 / desktop v0.3.156: Firefox 采集修复、推荐平台保底与备选 Provider 可诊断（2026-07-05）

后端源码走 `backend-v0.3.156`，浏览器插件走 `extension-v0.3.156`，桌面安装包走 `desktop-v0.3.156`。

- **第三方浏览器扩展报错不再触发桌面 Web 的红色故障横幅**：用户截图实锤——油猴类扩展往页面注入的脚本抛 `userScripts is not defined`，被 `/web` 的全局 error 监听器当成本站故障弹出「页面脚本出现问题」横幅（页面实际完全正常）。现在 error 事件按 `event.filename` 过滤，仅同源脚本（含内联与相对路径）才弹横幅，扩展 scheme（`chrome-extension://` 等）、跨域及被浏览器脱敏成空 filename 的错误降级为 console 警告；`unhandledrejection` 按 reason 调用栈同理过滤（栈中含本站帧则仍弹）。新增静态契约测试 `tests/test_desktop_web_error_banner.py`，过滤函数经 node 行为用例（12 例）验证。
- **初始化失败不再只剩一句「初始化过程中出错了」——异常详情三端可见**：社区用户截图实锤——引导初始化跑到一半崩溃时，三端只显示 `internal_error` 的兜底文案，真实原因只存在后端日志里，用户无从报告、维护者只能来回要日志。修复：`init_runs` 表新增 `error_detail` 列（存量库自动迁移），API 路径的失败落库同时带上细节——未知异常存 `类名: 首行消息`（截断 300 字，`_init_crash_detail`），`GuidedInitError` 存其人类可读 message（此前 API 路径直接丢弃、只有 CLI 打印）——经 `GET /api/init-status` 失败态的 `detail` 字段下发；`/setup/` 向导、桌面 Web、插件 popup 的失败文案统一为「通用文案（具体原因）」，未映射的 typed reason（`empty_history` / `empty_signals` / `profile_failed`）直接显示其 message 而非裸 code，三端 reason 映射表补上此前缺失的 `interrupted`（后端重启打断）与 `cancelled`；顺带修复 `/setup/` 失败态残留「正在检查 AI 服务…」行的状态错乱（run 展示中按进行 / 终态替换为对应提示）。新增协调器 detail 落库与复位回归、crash / GuidedInitError 两条真实 `/api/init` 端到端、`_init_crash_detail` 截断单测与 popup `describeInitFailure` 组合用例。
- **每日任务预算不再被误当成开关**：各源的 `daily_*_budget` 是「每 UTC 日、按任务类型的次数上限」（`0` = 不限，默认即 0），但用户与排障者把日志 `dy task budget exhausted: type=search, count=2, budget=1` 误读成内置的每轮限制。三处澄清：(1) 六个源（抖音 / 小红书 / YouTube / X / 知乎 / Reddit）的预算耗尽日志改写为自解释文案，点明是来自 config `[sources.<name>] daily_*_budget` 的每日 UTC 上限、`0 = 不限`（`count=` 改为 `used_today=`）；(2) 配置加载时对任一源 `daily_*_budget` 落在 1–4 的可疑值发一次进程级 WARN（提示这是每日次数上限不是开关、想不限请设 0）；(3) 桌面 Web 设置页各源预算行补一句中文说明，消除「填 1 = 打开该源」的误读。
- **修复 Firefox 打包版抖音 / Reddit 任务注入路径错误**：Firefox 签名 / 打包版从 `dist-firefox/` 作为扩展根打包，bundle 落在 `main/…`、`content/…`（无 `dist/` 前缀，见 `manifest.firefox.json`），但两个分发器与抖音 content 脚本把 `chrome.scripting.executeScript` / `chrome.runtime.getURL` 的路径写死为 `dist/…`，导致 Firefox 上文件不存在、注入永久失败（抖音 discovery 拿不到内容、Reddit fallback 注入失效）。改为构建期 esbuild `define` 注入 `__OBC_ASSET_PREFIX__`（Firefox = 空、Chrome = `dist/`），运行时按布局解析（node 单测无 define 时回落 `dist/`）。同时加固 MAIN-world 注入：Firefox 会对 MAIN-world 文件注入的完成值做 structured-clone，脚本执行成功却因返回值不可克隆而 reject——给注入 bundle 追加 `null;` footer 保证完成值可克隆，`injectFetchTapInto` 把 `non-structured-clonable` 错误归类为 `ok_uncloneable_result` 而非误报 error。
- **LLM 备选 Provider 静默失效可诊断化 + 同名备选保存被拒 + 新增「测试备选 Provider」按钮 + 设置编辑中不再被后台打回**：两条社区报告（「fallback 不生效」「配置页保存有问题」）的共同根因是 `[llm].fallback_provider` 的多个静默死状态——registry 的 fallback 链会无声丢弃「未注册（缺凭据）/ 非 chat-capable / 与主 Provider 同名」的备选（运行时静默丢弃本身是正确行为，问题在无处可见），且桌面 Web 在用户把备选选成与主 Provider 同类型时（网关用户配第二个 openai_compatible 的自然操作）静默丢掉备选面板填的 key / model / base_url、保存还报成功。修复四层：(1) `_collect_config_issues` 新增 `llm.fallback_provider` 校验（未知名（含网页翻译坏值提示）/ 与主 Provider 同名 / deepseek 等缺 `api_key`（保留 gemini 环境变量与 openai codex_oauth 豁免）/ `openai_compatible` 缺 `base_url` / ollama 缺 `model`+`base_url` 均 blocking，保存返回 400 不落盘），校验先于默认 provider 检查、默认 provider 本身写坏也不遮蔽；(2) `build_llm_registry` 对永远不会生效的备选按具体原因（同名 / 未注册 / 非 chat-capable）打一次 WARNING 兜底（env 覆盖 / 手改 config.toml 绕过保存校验时可见）；(3) 桌面 Web 备选下拉禁用与主 Provider 同名的选项并对旧同名配置显示行内警告（不静默改数据），`#llmProvider` / `#llmFallbackProvider` 补 `translate="no"`，备选子页新增「测试备选 Provider」按钮——`POST /api/config/probe-service` 新 `kind="llm_fallback"` 对备选 Provider 做精确单 provider 连通探测（未配置 / 同名以 ok=false 明确拒绝，不走 fallback 链）；(4) 运行时 `config_reloaded` 事件触发的全表单再水合在用户焦点位于设置表单内时跳过，未保存的编辑不再被后台事件悄悄清空。新增 config 校验单测、registry caplog 回归、PUT 同名 400 与 llm_fallback 探测端到端、桌面 Web 静态契约测试。
- **日志降噪：扩展调试探针与预期内瞬态 LLM 失败不再刷屏**：一份 3.5 小时用户日志里 2123 条 WARNING 有约 1900 条是 `api/app.py` 的 `[ext-debug] … url_probe`（扩展转发的调试探针，还带完整抖音 URL），此前以 WARNING 落盘——降为 DEBUG（桌面安装写 DEBUG 级文件日志，排障仍可 grep）。同一份日志的 41 条 ERROR 全部来自两类预期内瞬态状况：引导初始化期间尚未配置聊天 LLM（`LLMFallbackError: No provider was available`）与 provider 429 限流冷却（`LLMRateLimitError`）——这些路径本就会按周期重试，却在 `runtime/account_sync.py`、`runtime/feedback_scheduler.py`、`discovery/candidate_pipeline.py` 冒成吓人的 ERROR + traceback。新增 `llm/base.py::classify_llm_unavailability`（沿 `__cause__`/`__context__` 链、防环地判定 `no_provider`/`rate_limited`），三处调用点据此降级：no-provider 记 INFO、限流记单行 WARNING、其余仍走 exception（与 `discovery/engine.py` 既有「propagating transient failure」风格一致）。新增分类器单测（含链式 raise-from / 防环）与三处调用点降级回归。
- **推荐 serve 平台保底：非 B站 标签页不再空置数小时**：`get_pool_candidates` 每次只取一个按 tier+relevance 排序的 top-40 窗口，会话早期该窗口可能 100% 是 B站，即便池中知乎 / 小红书 / 抖音候选已可服务（实测 6 次 serve 有 4 次装载 40/40 全 B站，而池里有 300+ 混合候选）。`storage/database.py` 新增 `get_pool_candidates_for_platform`（复用同一 servable WHERE / guards / 排序 + 平台过滤）与 `list_servable_pool_platforms`；`recommendation/engine.py` 的 `serve()` 在装载窗口后、排除过滤前，对窗口内缺席的每个可服务平台补拉至多 5 条（按 bvid 去重）并记一行 INFO，下游 MMR / 多样化逻辑不变——只是窗口不再会静默漏掉有存量的平台。单平台池（常见纯 B站 安装）直接跳过，行为零变化。新增引擎补货 / 单平台跳过 / DB 方法 servability 守卫回归测试。

## v0.3.155 / extension v0.3.155 / desktop v0.3.155: 向量模型自诊自修、移动端直达与推荐池自愈（2026-07-05）

后端源码走 `backend-v0.3.155`，浏览器插件走 `extension-v0.3.155`，桌面安装包走 `desktop-v0.3.155`。

- **「Ollama 启用不了」从死重试变成可诊断、可一键自修**：用户日志显示 bge-m3 对 `/api/embeddings` 连续 500 一小时，UI 只有一个永远失败的「重试」按钮，看不出是 Ollama 没跑、模型没装还是模型损坏。三层修复：(1) 新增 `llm/ollama_diagnostics.py` 把向量模型不可用分类为 `not_running` / `model_missing` / `model_broken`（已安装但加载失败，如下载不完整 / 内存不足）/ `misconfigured`（provider 名无效——例如被浏览器整页翻译写坏成「奥拉玛」的配置）等原因，经 `GET /api/init-status` 的 `prerequisites.embedding_check` / `embedding_detail` 下发，插件初始化清单、`/setup/` 向导、桌面 Web 的向量模型行 hint 都展示具体原因和对应解法；Ollama embed 失败日志同时附带响应体错误（此前日志里只有裸 500，无法定位）。(2) 插件「语义去重未启用」横幅的启用按钮升级为一键修复：后端新增 `POST /api/embedding/repair`（仅本机、单飞后台任务）自动经 Ollama `/api/pull` 拉取缺失 / 重拉损坏的 embedding 模型，`GET` 同路径回报下载进度（按钮实时显示百分比，关面板下载继续），完成后就绪缓存立即过期、横幅与清单自动转绿。(3) 补上 `init-status` reason 梯子缺失的 not-trusted 分支——此前手机扫码 / 局域网查看初始化页时 `can_start=false` 但 reason 落到 `none`，三端全显示与全绿清单自相矛盾的「以下条件未满足」，现在正确返回 `local_only`（「只能在本机发起初始化」，三端文案早已备好）。(4) 修复插件 popup 对**硬性向量模型前置**的两处矛盾展示（第二台用户机器实锤的场景：桌面包默认配置了 ollama embedding → v0.3.137+ 起向量模型是服务端硬门槛，bge-m3 没就绪则初始化被拦）：popup 的 reason 映射表独缺 `embedding_not_ready`（desktop / setup 两端都有）导致被拦时只显示通用「以下条件未满足，补齐后再点一次」；初始化清单的向量模型行写死 `hard:false` + 「（推荐，非必须）」标签、无视 `embedding_required`——用户看到「非必须」的黄点却被拒绝启动。现在 popup 与另两端一致：required 时行转硬性 ✗ / 标签去掉「非必须」/ hint 不再出现「也能初始化」，reason 显示「向量模型还没就绪…」。(5) **初始化页面直接显示向量模型下载进度**：一键修复拉取模型期间，`init-status` 的 `embedding_check` 转为 `repairing`、`embedding_detail` 带实时百分比与 MB（另有 `embedding_repair_running/completed/total` 结构化字段），`/setup/` 向导与桌面 Web 的向量模型行 hint 随 3 秒轮询实时刷新（setup 预初始化态原本停轮询，现在下载期间保持轮询），用户能看到「正在下载 bge-m3：43%（245MB / 568MB）」而不是干等；两个 Web 端的向量行在「缺模型 / 模型损坏」时还提供「自动下载向量模型」按钮直接触发服务端拉取（插件端已有推荐页横幅入口）。misconfigured 文案区分「provider 名无效 → 重新选择」与「合法 provider 构建失败 → 检查 Key / base_url」。新增诊断分类 / repair 端点 / repairing 进度 / reason 回归、插件 hint 与硬门槛展示单测、两端静态契约测试；repair already_ok 短路已对真实本机 Ollama 端到端验证。
- **修复重启打断评估导致推荐池永久饿死**：用户日志实锤的链路——进程在候选评估中途重启后，残留的 `evaluating` 行计入补货目标（补货一直报 `target_reached` 不拉新）、drain 只领 `pending_eval`、池上限清理又保护 in-flight 行，而启动时的既有回收只处理 ≥30 分钟的旧 claim（重启孤儿只有秒龄，漏网后再无任何周期清扫）→ `pool_available=0` 永远不恢复。修复：进程内首个候选 pipeline 构造时全量回收孤儿 `evaluating`（config reload 重建不重扫），每次 drain tick 先回收超 30 分钟的 stale claim（评估任务中途死亡也能自愈），DB 层回收同时覆盖 `claimed_at` 为 NULL 的不可老化行。新增 DB 与 pipeline 回归测试。
- **浏览器网页翻译不再能写坏 provider / 日志级别配置（根因修复）**：桌面 Web 设置页 8 个下拉框（Embedding / 备选 Embedding / 四个模块 Provider / 两个日志级别）的 `<option>` 没有 value 属性——Chrome/Edge 整页翻译重写选项文本后，`select.value` 回退到译文，「奥拉玛」「双子座」这类机翻值被直接存进 config.toml 并静默关闭 embedding。三层修复：全部 option 补显式 value、这些 select 加 `translate="no"`；`_collect_config_issues` 对 `llm.embedding.provider` / `fallback_provider` 的未知值按 blocking 拦截（保存返回 400 不落盘，提示关闭网页翻译重选；已有坏配置仍可正常启动并由 embedding 诊断标为 misconfigured）。新增校验单测与「所有 option 必须带 value + 关键 select 必须 translate=no」静态契约测试。
- **移动端点击推荐直接拉起目标平台 App**：此前手机 Web（`/m/`）点击推荐是新标签页打开平台网页，用户落在 B站 / 小红书等的「打开 App」中间页上，还要再点一次才能进 App。现在移动端点击会先按内容 URL 构造平台深链（B站 `bilibili://video/`、小红书 `xhsdiscover://item/`（携带 `xsec_token`）、抖音 `snssdk1128://`、YouTube `vnd.youtube://`、X `twitter://`、知乎 `zhihu://`）直接尝试拉起 App，1.6 秒内页面未被切走（未装 App 或 WebView 拦截 scheme）再回落网页，且回落**永不占用当前页**：优先新标签页打开，弹窗被拦（手势过期，iOS Safari 必拦）则显示页内「打开网页版」提示条；iOS「在 App 中打开?」系统确认框挂起期间（`blur`）暂停回落计时、关闭后续 0.9s 重试，修复拉起成功却被误跳当前页需手动后退的问题。桌面端与无法解析深链的地址（b23.tv / xhslink 短链、Reddit）行为不变。覆盖推荐整卡点击 /「打开」按钮 / 惊喜推荐「看看」/ 消息通知 / 稍后再看与收藏列表六个入口，点击上报不受影响。新增 `web/js/app-launch.js` 深链构造与 UA 判定的 node 测试及 pytest CI 包装，真机（iOS + B站/小红书/抖音/知乎/YouTube/X）与 Playwright 真后端 E2E 验证。
- **手机版入口在插件与桌面 Web 明显化**：用户反馈插件 popup 顶部那个纯二维码小图标存在感太低，找不到手机页面入口。现在插件 popup 顶部改为「手机图形 + 手机版文字」按钮（保持与相邻图标同款白底样式，靠文字标签显眼；手机图形带听筒线 + Home 点，替换原认不出的二维码字形；点开仍是原二维码浮层），旁边「打开 Web 版」按钮的通用外链箭头图标同步换成显示器图形，与手机图标形成「电脑版 / 手机版」直观配对；桌面 Web（`/web`）顶栏新增「手机版」黑色实心文字胶囊（与搜索 / Star 按钮同设计语言）——此前桌面 Web 完全没有手机页入口——点开居中对话框展示扫码二维码；对话框内含二维码（新增自包含 `mobile-qr.js` 生成器，从插件 `popup-qr.js` 移植、无外部依赖）、局域网地址与复制按钮，地址用后端 `/api/health` 的 `lan_ip` 构造（页面自身跑在 127.0.0.1 时手机扫本机地址打不开），拿不到局域网 IP 时给出 `--host 0.0.0.0` 排查提示；首次访问在入口下方弹「手机扫码，躺着刷推荐 →」深色气泡（点击直接开二维码）并在按钮角标 terracotta 圆点，打开过一次即永久消失（`openbiliclaw.webui.mobileQrSeen`），确保新用户一眼发现。新增插件静态契约测试 `mobile-entry.test.ts` 与 `tests/test_desktop_web_mobile_entry.py`，桌面 Web 已跑真后端 Playwright 验证（二维码渲染 + 局域网地址正确）。
- **设置页「当前 Cookie / 登录凭据」只读查看区不再伪装成输入框 + Reddit 补上手动粘贴通道**：用户群实际反馈把该区当成 Cookie 粘贴框（readonly textarea 粘不进去，被当成 bug）。三层修复：(1) 只读框改为虚线边框 + 置灰底色 + 默认光标，平台名旁新增「只读」标签，每行新增「复制」按钮（无凭据时禁用，成功弹 toast）；(2) 区块说明改为完整解释接入模型——手动粘贴只提供给后端直接带 Cookie 请求平台接口的源（B站 / 抖音 / X / Reddit），小红书 / 知乎走插件浏览器登录态、YouTube 走公开接口，无需也无法手动填 Cookie，消除"为什么有的平台有输入框有的没有"的疑惑；(3) Reddit 补上此前缺失的手动粘贴通道：设置页新增 Reddit Cookie 覆盖输入框（需含 `reddit_session`），`PUT /api/config` 的 `sources.reddit.cookie`（API-only 字段，不落 `config.toml`）路由到 rdt-cli credential store（与插件自动同步同一存储），缺 `reddit_session` 的粘贴以 400 `missing_reddit_session` 显式拒绝而非静默丢弃；插件 side panel 的 Reddit 卡片同步新增同款 Cookie 粘贴框（无 config 回显、纯粘贴入口，留空不覆盖）。新增两条 PUT config 回归测试与插件契约测试；Playwright 对静态页验证只读区结构 / 复制链路 / 只读样式 / Reddit 输入框可编辑。
- **YouTube 搜索 discovery 补 yt-dlp 匿名兜底**：`yt_search` 此前只有 scrapetube 一条腿——YouTube 改版打坏 scrapetube 时搜索静默返回空，YouTube 候选供给慢性饿死且日志难定位。现在 scrapetube 搜索异常或 0 条时 fallback 到 `yt-dlp` 的 `ytsearchN:` 匿名搜索（与频道路径既有 fallback 同构，共享 flat-extract 配置，仅拉元数据不下载媒体），两层日志明确标注降级原因。经真实网络冒烟验证 yt-dlp 搜索路径可用；trending 刻意不加 yt-dlp 层——实测 `/feed/trending` 已被 YouTube 下线（重定向首页）、yt-dlp flat 解析对 shelf 型 topic 页拿不到条目，现有 InnerTube → topic 页两层仍是 trending 的有效链路。新增 scrapetube 失败 / 空结果降级与 `ytsearch` 映射回归测试。

## v0.3.154 / extension v0.3.154 / desktop v0.3.154: 第三方网关、国内平台直连与自动更新完备化（2026-07-05）

后端源码走 `backend-v0.3.154`，浏览器插件走 `extension-v0.3.154`，桌面安装包走 `desktop-v0.3.154`。


- **Windows 桌面包支持系统 SOCKS 代理**：用户系统设置 `ALL_PROXY` / `HTTPS_PROXY=socks5://...` 时，冻结包启动阶段创建 OpenAI / 兼容 LLM 客户端会触发 `httpx` 的 SOCKS 路径；此前默认依赖未安装、PyInstaller 也未显式收集 `socksio`，导致启动弹出 `Failed to execute script 'entry'`，浏览器随后只能看到 `127.0.0.1 refused`。现在默认依赖改为 `httpx[socks]`，spec 增加 `socksio` hidden import，并补打包回归测试。
- **运行时可选依赖补齐**：专项审计发现两处同类隐患：(1) `/api/runtime-stream` 是插件 / Web UI 的核心通道，但 `websockets` 只在 `dev` extra，桌面包按 `.[packaging]` 构建时只装裸 `uvicorn`，WebSocket 协议实现可能缺失；(2) `discovery.multimodal` 直接 import `PIL`，此前普通运行路径靠 `bilibili-api-python` 的传递依赖碰巧带入 Pillow。现在默认依赖显式加入 `websockets>=13` 与 `Pillow>=10.0`，PyInstaller spec 增加 `uvicorn.protocols.websockets.websockets_impl` / `websockets` hidden import，并补元数据与打包回归测试。
- **未初始化客户端稳定进入引导流程（全入口审计修复）**：针对「未初始化的客户端必须稳定看到引导初始化」目标做了一轮四入口审计并修复：① `/web` 页面比后端先加载（冻结包启动竞速）后永久空白——runtime-stream 首次连上时若 initStatus/runtimeStatus 均为空则触发补水重拉；② 安装包入口 `webbrowser.open` 在 uvicorn 绑定前执行必然 `ERR_CONNECTION_REFUSED`——改为后台线程轮询 `/api/health`（≤30s）后再开页，并读 `/api/init-status` 决定落地页（已配置但从未初始化 → `/setup/` 而非 `/web/`；「已有实例」分支同样 init 感知）；③ `/setup/` 向导刷新后静默落回第 0 步——load 时恢复现场（running → 直挂实时进度，initialized → 完成页 / 等待态），已存 key 的 provider 留空即沿用不必重贴，首池等待态新增「先进入应用 →」逃生链接（此前用户被永久停在 95% 禁用按钮上）；④ 插件对「在线但未初始化」零信号（badge 被清空、与健康态视觉一致）——新增三态 badge 决策表（灰 `!`=后端未启动、橙 `!`=未初始化点击开始引导、清空=健康），WS 连上用零探针的 `/api/runtime-status` 刷新，`init_completed`/`refresh.pool_updated` 事件即时清除；⑤ Docker 文档把方式 A/B 用户指向容器内被 `unsupported_runtime` 封锁的「开始初始化」按钮——文档 / compose 注释 / 前端文案统一改为「/setup/ 完成配置与前置检查 + 宿主机 `docker exec … openbiliclaw init`」；⑥ `start`/`serve-api` 未初始化时启动前打印引导入口 WARN 面板。新增 packaging 落地页判定 / 健康等待单测、`/setup` 恢复现场与逃生口 Playwright E2E、badge 决策表单测与多条静态契约测试。
- **引导初始化审计 P2 清尾**：① 插件 popup 在「后端在线但 runtime-status 瞬时失败 + 推荐恰好为空」的窄窗口不再把已初始化后端误判成未初始化（会闪出吓人的 init CTA）——runtime 快照缺失时改渲染「后端状态暂时没读到，稍后自动重试」过渡态，真未初始化仍由 service worker 的 badge 通道兜底；② 未初始化期间行为事件被后端 200 + `not_initialized` 静默消费丢弃的路径，现在会点亮工具栏橙色未初始化 badge（事件仍不重试缓冲——init 会整体重拉历史），新增 `flushResponseReportsUninitialized` 纯函数与单测；③ `/setup/` 在开启访问密码的远程未登录浏览器里保存 AI 配置不再死在裸「HTTP 401」——提示先去 `/web` 输入访问密码登录再回来（init 端点公开但 `/api/config` 受会话门禁的既有不对称按设计保留）。
- **桌面 Web 引导初始化不再因 runtime-status 缺失而永久隐藏**：`/web` 未初始化空状态的引导面板闸门原先只认 `state.runtimeStatus.initialized=false` 一条来源——hydrate 里对 `/api/runtime-status` 的二次拉取失败时被 `requestJson` 吞成 `null`（`.catch()` 兜底是死代码，永远不会触发），随后任意不带 `initialized` 字段的 runtime 事件 / 消息合并又会把该字段默认成 `true`，导致后端明明未初始化、`/api/init-status` 也明确报 `initialized=false`，引导却永远不出现，用户只能看到「推荐都已处理」空态。现在闸门把 `init-status.initialized=false` 作为权威来源之一（仍受“初始化后信号”与已有推荐卡守卫），hydrate 兜底改为 `|| runtime` 复用 Promise.all 快照；`tests/test_web_guided_init.py` 新增两条静态契约测试防回归。
- **画像写入契约说明收敛**：`MemoryManager.propagate_event()` 文档改为明确“只持久化事件”，删除会误导维护者以为 memory 层隐式刷新偏好 / 觉察 / Soul 的旧 TODO；`docs/modules/soul.md` 和 `docs/modules/memory.md` 同步说明初始化后的普通行为增量由 API/runtime 显式转成 `ProfileSignal` 并送入 `ProfileUpdatePipeline`。
- **开代理不再导致 B站 显示"未登录"：探测恒直连 + 失败原因可见 + 文案修正**：用户群反馈开着代理时引导初始化一直报"B站 未登录"（浏览器里明明已登录），且取消勾选 B 站后清单反而显示"B站 已登录（未勾选…）"，只能靠关代理恢复。三层修复：(1) 无痛兼容代理——`BilibiliAPIClient` 全面 `trust_env=False`，访问 B站 恒直连、不再继承环境变量和 Windows / macOS 系统代理（httpx 默认两者都读，代理出口 IP 常触发 B站 风控；B站 是国内域名直连恒可达），Clash 等代理开着不用关，LLM 流量照走代理；网络确实无法直连 B站 的场景（企业内网等）可用新配置 `[bilibili] proxy` 显式指定专用代理（`config.example.toml` / `save_config` / 文档同步）。真实坏代理环境 E2E 验证：同一环境修复前 `failed`、修复后 `ok`。(2) 探测失败原因可见——`AuthStatus.network_error` 区分传输层失败与 Cookie 真失效（nav `-101` 抛 `BilibiliAuthExpiredError` 归 Cookie 类，不误导查代理；该分类靠真实请求 E2E 才暴露），`InitPrereqs` 把具体失败原因经 `prerequisites.bilibili_detail` 下发（直连失败提示查本机网络 / TUN 全局模式加直连规则；显式代理失败提示检查该代理），`POST /api/init` 的 409 也带 `detail`。(3) 文案缺陷——setup 向导与桌面 Web 的 checklist B 站行 label 原是固定写死"已登录"的条目名，仅靠 ✓/✗/• 符号表意，取消勾选降级为中性"•"后用户按字面读成"已登录"；现在 label 按探测真实结果措辞（"B站 登录检测未通过"），hint 展示 `bilibili_detail`。新增客户端代理旁路 / 探测分类 / 配置 round-trip 单测与两端静态契约测试防回归。
- **国内平台请求全面绕过系统代理（B站 修复的同类扩散）**：代码库排查发现另外两处裸 httpx 客户端仍会继承环境 / 系统代理：抖音直连客户端 `DouyinDirectClient` 和封面抓取核心 `fetch_cover_bytes`。抖音同为国内域名，默认客户端改为 `trust_env=False` 恒直连；封面抓取按主机分流——国内 CDN（hdslb / xhscdn / pstatp / douyinpic / douyinvod）恒直连（开代理时封面变慢 / 被拦的隐患消除），境外 CDN（ytimg / ggpht）保持继承环境代理，需要代理才能拉 YouTube 封面的用户不受影响。真实坏代理环境 E2E 验证：B站 封面绕过代理直连成功，YouTube 封面按预期走代理。
- **候选评估批量凑批与缓存上限**：`[scheduler]` 新增 `eval_min_batch_size` / `eval_max_wait_seconds`，raw candidate drain 可把零散候选合并成更满的评估批次，同时保证单个候选最长等待有上限；discovery eval cache 改为 4096 条 LRU，避免长时间运行时无界增长。`classify_pool_backlog` 默认批量从 10 提到 30，60 条 legacy 待分类内容默认拆成 2 次 LLM 调用。
- **Discovery eval embedding 预过滤进入 shadow rollout**：`[discovery].eval_prefilter_mode` 新增 `off` / `shadow` / `enforce` 三档，默认 `shadow` 只记录 `prefilter-shadow` would-filter 候选并继续送 LLM；确认 would-filter 中几乎没有高于 admission 门槛的内容后可切 `enforce`，由本地 embedding 相似度提前缓存低分并跳过 LLM。`explore` 候选始终豁免，enforce 单批过滤超过 50% 时自动 fail-open。
- **LLM token diet 收口到评估 / 表达热路径**：`discovery.evaluate_batch` 坏输出不再整批退化成 N 次单条调用；当前 landing 实现只对成功响应里的缺失 / malformed member 做有界 subset/split retry（最多深度 3、额外 6 请求），provider 异常原样传播。LLM caller bucket 补齐 `discovery.keyword*`、`discovery.x*`、`discovery.douyin*`、`runtime.bilibili_extension_search*`、`pool_purge*`、`api.sentiment*`，整条高频链路都能用 `[llm.discovery/evaluation/recommendation/soul]` 分层模型配置覆盖。内容评估画像当前实验性使用 compact summary（20 核心 / 48 兴趣 / 32 域 × 16 specifics / 12 recent，长期避雷不裁剪），每条候选另带从画像块外长尾（权重 49..256 名）召回的最多 3 个 `related_interests` 兴趣名（画像不超过 48 兴趣时零开销），digest 覆盖 compact 画像和精确召回池。此前 replay divergence PASS 已因 source-context 污染作废，64 / 12、增长后画像上的 80 / 16 与收益仅 `0.67%` 的 96 / 16 最终 replay 均未过门；48 / 16 仍须新 artifact 验收。推荐表达 / legacy 分类共用同一个 `compact_content_prompt_profile_summary()` 收口，`interests=` 内容相关替换保留；原定正文 200+100 截断被后续严格 Reddit 100×3 gate 否决并完整回滚，discovery 与 recommendation 均保留完整 `body_text`。
- **Docker 启动不再因缺少宿主代理而退出**：用户反馈 Docker 部署后端会检测 `host.docker.internal:7897`，端口上没有代理就直接退出容器。根因是 `docker_runtime.can_connect()` 契约上应返回布尔（端口可达性），实现里却让 `socket.create_connection` 的 `ConnectionRefusedError` / 超时 / DNS 异常直接抛出——容器内 `main()` 引导阶段探测宿主 Clash 代理失败时异常一路冒泡，进程在 `os.execvpe` 启动 `serve-api` 之前就崩溃，表现为容器一启动就退出。现在 `can_connect()` 捕获 `OSError` 系列并返回 `False`，无代理时 `resolve_optional_proxy_env()` 正常返回空更新、跳过代理注入，容器照常启动。此前所有相关测试都用返回干净布尔的 mock `can_connect`，掩盖了真实实现会抛异常；新增两条使用真实 `can_connect` 打死端口的回归测试。顺带加固同类隐患：`bootstrap_runtime_environment` 里 `int(OPENBILICLAW_PROXY_PORT)` / `float(OPENBILICLAW_PROXY_TIMEOUT)` 遇到用户填的空值或非法值也会抛 `ValueError` 崩在启动前，现改为空值回落默认端口 7897、并将整个可选代理探测步骤包进守卫——任何异常只打印一行 stderr 并跳过，绝不阻断 `serve-api` 启动。
- **第三方 API 网关适配（issue #72）**：两项打通。(1) Claude 自定义 base_url——`[llm.claude].base_url` 此前会被解析但静默忽略（`ClaudeProvider` 构造 `AsyncAnthropic` 时不传、`save_config` 白名单也不落盘），现在全链路穿透，可指向任何 Anthropic 协议（`/v1/messages`）中转网关；`/setup/` 引导页选 Claude 时展示可选 Base URL 输入框，桌面 Web 设置页原有的通用 Base URL 字段随之真正生效。(2) OpenAI Responses API——`[llm.openai]` / `[llm.openai_compatible]` 新增 `api_flavor` 字段（`""`/`"chat_completions"` 默认走 `/v1/chat/completions`，`"responses"` 走 `/v1/responses`），适配只给 GPT 模型开放 Responses 端点的第三方网关：system 消息映射为 `instructions`、`max_tokens`→`max_output_tokens`、json_mode 走 `text.format`，usage 的 `input_tokens_details.cached_tokens` 归一到 `cached_input_tokens`（`openbiliclaw cost` 缓存命中统计不受影响）；对拒收 `temperature` 的推理系模型（gpt-5 家族）自动降参重试一次，空内容时与 chat 路径同样去掉格式约束重试。非法 `api_flavor` 值会被 `_collect_config_issues` 以 blocking 级拦下。各端配置入口同步适配：`/setup/` 引导页 openai_compatible 显示「接口协议」下拉框、桌面 Web 设置页默认与备选 provider 面板各新增「API 协议」下拉框、`openbiliclaw init` 终端向导 Claude 分支新增可选 Base URL 提问（回车 = 官方）；「测试 LLM」探针经 `_apply_llm_update` 自动携带 `api_flavor`。新增 provider / registry / config 三层回归测试。
- **自动更新按安装形态完备工作（git / 安装包 / Docker）**：用户实测 git 安装点「立即应用」被 `untrusted_remote`（"git 远端不在允许列表"）拦截——根因是允许列表用**精确字符串匹配**，`git clone` 时少写 `.git` 后缀、或 HTTPS/SSH 拼法与列表条目不一致就永久卡死。三处修复：(1) 可信 remote 改为**规范化比较**（`_canonicalize_remote_url`：`.git` 后缀可选、`https://` 与 `git@…:`/`ssh://` 等价、大小写不敏感；镜像/代理包装 URL 不自动折算成官方地址，镜像用户把镜像 URL 加入 `auto_update_allowed_remotes` 即可）；(2) **守卫拒绝不再双重静默**——此前 apply 被任何 guard 拦下既不写日志也不落 `last_error`，只有 UI 一行文案，现在每条拒绝都 `logger.warning` 写明细（含实际 remote URL、脏文件列表、分叉 tag 等）并把原因写入 `last_error` 供状态卡展示；(3) 新增 **`docker` 安装形态**（`detect_install_mode()` 经 `is_running_in_container()` 判定，Dockerfile 内置 `OPENBILICLAW_IN_CONTAINER=1` 兜底 containerd/K8s 无 `/.dockerenv` 的场景，且优先于 `git`——容器里挂载检出也不误入自更新），容器与冻结包一样跑 check-only 提醒循环（跟踪 `backend-v*`，GHCR 镜像随后端版本同号发布），发现新版时插件 popup 与桌面 Web 设置页提示 `docker compose pull && docker compose up -d`，误触 apply 以 `docker_install_mode` 明确拒绝而非笼统的"安装方式不支持"。插件 popup 更新卡新增按形态的升级指引行（git=就地应用 / frozen=下载安装包 / docker=拉镜像）。已做全真端到端验证：0.3.152 克隆（无 `.git` 后缀 remote）旧码复现 blocked → 新码从扩展 popup 点击应用 → 真实快进 0.3.153 + 依赖同步 + 自重启约 9 秒 → popup 显示已最新；docker 形态经 HTTP / popup / 桌面 Web 三面验证。新增 URL 规范化参数化单测、等价拼法 apply 放行、docker 形态检测 / check-only / 拒绝、守卫日志与 last_error 回归测试；插件契约测试同步；FAQ 新增存量被卡用户一次性解锁指引。

## v0.3.153 / extension v0.3.153 / desktop v0.3.153: Docker 预构建镜像与 LLM 探活降噪（2026-07-04）

后端源码走 `backend-v0.3.153`，浏览器插件走 `extension-v0.3.153`，桌面安装包走 `desktop-v0.3.153`。

- **已初始化实例不再反复发 LLM 探活请求**：用户反馈账单里持续出现 5-in/10-out 的 DeepSeek 小请求——来源是 `GET /api/init-status` 每次都跑真实 `health_check()`（"hi" 补全，成功仅缓存 30s），开着 `/setup/` 或桌面 Web 等首池页面时每 30s 烧一条。现在 `initialized && !running` 时改用 `InitPrereqs.peek_chat()` / `peek_bilibili()` 只读缓存值，不发探针；`POST /api/init`（含 force）仍实时复验。chat 探针成功 TTL 从 30s 放宽到 300s，降低预初始化阶段轮询成本。桌面 Web 初始化完成后不再渲染前置 checklist，避免展示未探测的缓存态。新增回归测试断言已初始化状态读取零探针调用。
- **Docker 安装现代化**：新增 `.github/workflows/release-docker.yml`，`backend-v*` tag 自动构建并推送多架构（amd64 + arm64）镜像到 GHCR；新增自包含的 `docker-compose.prebuilt.yml`，用户 `curl` 一个文件即可 `docker compose up -d`，无需克隆源码或本地构建。Dockerfile 改为依赖分层（先按 `pyproject.toml` 装依赖再拷源码 `pip install --no-deps .`），源码变更后重建从整装依赖缩短到十几秒。`docker-compose.yml` 后端对 Ollama sidecar 的依赖从 `service_healthy` 放宽为 `service_started`，bge-m3 首拉失败或缓慢不再永久卡死后端，`/setup/` 前置检查会显示 embedding 就绪状态。`docs/docker-deployment.md` 重写快速开始（预构建镜像 / 源码构建 / 终端向导三路径，主路径统一收口到 `/setup/` 图形化引导）、修正容器内不存在的 `uv run` 命令、修正安装向导来源覆盖描述。GHCR 首个镜像已发布且匿名可拉取；完整用户视角 E2E 通过：`curl` compose 文件 → `docker compose up -d` → 后端秒级健康 → sidecar 后台拉取 bge-m3 → `embedding_ready` 约 140s 自动就绪。
- **聚合 Release 页新增 Docker 渠道行**：`sync-aggregate-release.sh` 匿名探测 GHCR 上该版本 manifest 是否可拉取，可拉取才展示镜像引用与 compose 下载指引（与其他 channel 同样不回填）；`release-docker.yml` 推完镜像后在 tag push 场景自动重跑聚合页同步；顺带修正 Firefox XPI 缺失时的病句文案。
- **README / 文档首页 / GitHub About 优化**：README 中英双版第一屏瘦身（删除开发版 E2E 段落、快速开始压缩为四短步、「最近更新」只保留用户可感知亮点并与更新日志板块去重）、核心特性与架构概览精简并链接模块文档、新增 Release / CI 徽章与结尾 star 引导、英文版补 RedNote / Chinese TikTok 平台注释；`docs/index.md` 拆分「用户 / 开发者」两个区块并去掉重复条目；新增 `docs/faq.md` 常见问题页（含 Docker 部署条目）；官网首页安装区新增「Docker 预构建镜像」双语面板；GitHub About 补 Reddit、缩短为卖点前置的双语文案。
- **插件与桌面安装包同步发布**：插件版本提升到 `extension-v0.3.153`（功能代码与 `extension-v0.3.152` 一致，纯版本号对齐）；桌面安装包提升到 `desktop-v0.3.153`，冻结包用户直接获得本轮探活修复与桌面 Web checklist 调整。

## v0.3.152 / extension v0.3.152 / desktop v0.3.152: 桌面启动自愈与动态惊喜阈值（2026-07-04）

后端源码走 `backend-v0.3.152`，浏览器插件走 `extension-v0.3.152`，桌面安装包走 `desktop-v0.3.152`。

- **macOS 安全阻挡提示收敛**：README、官网首页、desktop Release notes 和 DMG 内 `首次打开说明 First Launch.html` 统一改成“右键 / Control-click 打开 → 隐私与安全性仍要打开 → 已损坏时清除 quarantine”的顺序；用户侧不再被提示执行额外 `codesign` 命令，下载后安装包内也能直接看到同一段说明。
- **插件更新失败原因可见**：side panel 设置页点击“立即应用”后，如果后端自动更新被 `dirty_worktree`、`untrusted_remote`、`branch_not_fast_forwardable` 等安全守卫拒绝，插件会展示本地化原因并刷新状态卡，不再只提示“后端更新未能开始”。
- **更新入口严格区分安装渠道**：side panel 和桌面 Web 的版本面板现在只有在后端明确报告 `install_mode="git"` 且存在 `backend-v*` 更新时才显示“立即应用”；`install_mode="frozen"` 或最新 tag 为 `desktop-v*` 时只显示 Release 下载入口，空 / 未知安装方式不再误入源码自动更新分支。
- **桌面安装包坏配置自愈**：Windows / macOS 冻结包启动时会先校验用户数据目录里的 `config.toml` 与 `config.local.toml`。若文件无法按 TOML 解析，或 TOML 结构导致运行时配置对象无法构建，入口会把坏文件改名为 `*.invalid[.N]` 备份，从打包内置 `config.example.toml` 重新生成默认 `config.toml`，并打开 `/setup/` 重新初始化；`data/` 目录不会被移动或删除。
- **惊喜推荐改为池内 Top 10% 动态阈值**：`precompute_delight_scores()`、runtime 主动推送、pending-batch、CLI 和普通推荐池的 delight 占位排除都改用动态门槛。默认底线仍是 `0.70`，低探索开放度用户底线仍是 `0.80`；正式候选池样本不少于 20 条时，会取 `max(profile floor, 当前池内 Top 10% 分数边界)`，避免普通高分内容被过早包装成“惊喜推荐”。生产库副本验证还暴露了旧版 `delight_score` 标尺残留，因此 backfill 现在会重新领取并同步 `delight_score != relevance_score` 的历史行，包括 `shown` 且已有普通推荐历史的行。
- **聚合 Release 不再误列缺失的 Firefox XPI**：`sync-aggregate-release.sh` 现在只有在实际收集到 `openbiliclaw-extension-v*-firefox.xpi` 资产时，才把 signed XPI 写进 `openbiliclaw-v*` 聚合页；未启用 AMO signing 时只列 Chrome zip 与 Firefox 临时加载 zip，避免用户看到不存在的下载文件。
- **聚合 Release 只收同版本资产**：`openbiliclaw-vX.Y.Z` 现在只引用 `extension-vX.Y.Z` / `desktop-vX.Y.Z` 的包；某个 channel 尚未完成时显示未发布，不再从上一版 release 回填旧 `.zip` / `.dmg` / `.exe`。旧包清理同时把 GitHub API 超时后返回的 `not found` 视为幂等成功，避免删除实际已生效却导致 workflow 失败。

## v0.3.151 / extension v0.3.151 / desktop v0.3.151: LLM 探测诊断与发布渠道版本对齐（2026-07-02）

后端源码走 `backend-v0.3.151`，浏览器插件走 `extension-v0.3.151`，桌面安装包走 `desktop-v0.3.151`。

- **LLM 探测诊断补强**：配置页和 CLI 探测延续 `v0.3.150` 的 reasoning-only 诊断语义，并补充更清晰的 probe 失败信息，方便用户区分模型仅返回思考内容、最终内容为空和真实服务不可用。
- **发布渠道版本号对齐**：浏览器插件、后端源码和桌面安装包统一提升到 `0.3.151`，GitHub Release 聚合页、插件包和桌面安装包使用同一版本号，减少用户在手动插件包、Chrome Web Store 与桌面安装包之间比对版本的成本。
- **发布流程文档同步**：收紧平台来源发布 checklist，并刷新 README / 文档索引中的当前版本标识，确保本轮插件包、后端源码 tag 和桌面安装包 tag 对外一致。

## v0.3.150 / extension v0.3.101 / desktop v0.3.150: Reddit rdt-cli 默认后端与发布包同步（2026-06-30）

后端源码走 `backend-v0.3.150`，浏览器插件走 `extension-v0.3.101`，桌面安装包走 `desktop-v0.3.150`。

- **Reddit discovery 默认切到 rdt-cli**：`rdt-cli>=0.4.1` 纳入默认运行时依赖，源码安装、AI 一键安装、Docker `pip install .` 和桌面 PyInstaller 安装包都会默认携带；日常 discovery 默认后端为 rdt-cli，插件仍负责 bootstrap 初始化信号，并在命令后端不可用、未登录或用户显式选择时作为 fallback。
- **插件同步 rdt credential**：已连接 OpenBiliClaw 插件会尝试把浏览器里的 `reddit_session` 同步到 rdt-cli credential store，`rdt login` 仅作为插件不可用或浏览器 Cookie 不可读时的手动 fallback；后端状态页和 CLI 文案会明确区分 rdt 缺凭据、插件 fallback 可用和真实登录态任务结果。
- **安装包与冻结包依赖对齐**：桌面打包显式收集 `rdt_cli` 及其 lazy 依赖，且在冻结包里提供 in-process fallback，避免只有 Python 包而没有 `rdt` console script 时 Reddit discovery 不可用。
- **LLM 探测预算与 reasoning-only 诊断**：配置页 LLM 探测和 `LLMProvider.health_check()` 的共享输出预算从 1024 提到 4096。`DeepSeekProvider` 在 `reasoning_effort=""` 时会向 DeepSeek 请求体写入 `thinking={"type":"disabled"}`，而不是省略 thinking 字段；OpenAI-compatible / DeepSeek / OpenRouter / Ollama native 若只返回 `reasoning_content` / `reasoning` / `thinking` 而没有最终 `content`，现在仍判失败，但错误会明确提示“returned reasoning but no final content”并带 `finish_reason`，避免误以为服务完全没返回。
- **真实环境验证补充**：本地真实插件登录态完成 `fetch-reddit --mode bootstrap`、四个 `discover-reddit*` smoke 和正式 `discover --source reddit`；当前浏览器会话里 rdt credential 仍未同步到 `reddit_session` 时，fallback 插件路径能完成事件拉取和 discovery。

## v0.3.149 / extension v0.3.100 / desktop v0.3.149: Reddit 来源接入（2026-06-30）

后端源码走 `backend-v0.3.149`，浏览器插件走 `extension-v0.3.100`，桌面安装包走 `desktop-v0.3.149`。

- **Reddit 插件登录态 discovery 源**：新增 `reddit_tasks`、`RedditDiscoveryProducer`、`fetch-reddit`、`discover-reddit*` 和 `discover --source reddit`；默认后端为 OpenBiliClaw 浏览器插件，支持 search / hot / subreddit / related 四个独立分支，每个分支默认每日 300 条预算。
- **Reddit 正式 discover 接入统一候选池**：插件任务回传的帖子 / 评论会转换为 `source_platform="reddit"` 的 `DiscoveredContent`，以 fetch-only 方式写入 `discovery_candidates`，后续由统一 evaluator 混源评估，避免真实 E2E 被单次 LLM 批量评估阻塞。
- **知乎 / Reddit search query generation 复用统一 planner**：`zhihu-search` 和 `reddit-search` 都进入 `KeywordPlanner` 合并关键词生成和静态 `<supply_advantage>` 表，search 分支通过 `KeywordFetchCoordinator.claim(<platform>)` 消费并透传 `source_keyword_id`；关键词池为空时仍回退画像关键词。
- **三端配置页与推荐卡适配**：插件 side panel、桌面 Web 和移动 Web 支持 Reddit 来源开关、source modes、四分支预算、候选池占比、来源状态和文字卡 fallback；配置保存后进入 `runtime.source_policy` 与 candidate pool 配额。
- **Reddit guided init 画像信号**：Reddit 不再是 discovery-only 来源；`init --yes-reddit` / 图形化勾选 Reddit 会通过插件登录态读取 saved / upvoted / subscribed subreddit，分别转成 `favorite` / `like` / `follow` 事件纳入 `analyze_events()` / `build_initial_profile()`，Reddit-only 初始化只要真实拉到信号即可完成。CLI、`/api/init`、插件推荐 tab、桌面 Web 和 `/setup/` 均取消旧的 `no_profile_signal_sources` 拦截；`fetch-reddit --mode bootstrap` 可单独端到端验证事件拉取。API schema 默认值继续对齐实际 `[sources.reddit]` 的 `backend="extension"` 与四分支 discovery 预算 300。
- **真实环境验证**：本地 worktree API + 已安装插件登录态完成 `fetch-reddit`、`discover-reddit`、`discover-reddit-hot`、`discover-reddit-subreddit`、`discover-reddit-related` 和 `discover --source reddit --limit 2`，四分支正式 producer 返回 `reddit-hot` / `reddit-related` / `reddit-search` / `reddit-subreddit` 候选并入池。
- **新平台来源接入指南**：新增 `docs/platform-source-integration.md`，把知乎 / Reddit 接入经验沉淀为后续新增来源的标准 checklist。

## v0.3.148 / extension v0.3.99 / desktop v0.3.148: LLM 余额熔断与推荐避雷兜底（2026-06-28）

后端源码走 `backend-v0.3.148`，浏览器插件走 `extension-v0.3.99`，桌面安装包走 `desktop-v0.3.148`。

- **DeepSeek / OpenAI-compatible 余额不足不再重试放大**：HTTP 402、`Insufficient Balance`、`payment required`、`billing`、余额不足等 provider 余额 / 账单失败现在归一为 `LLMRateLimitError`。Provider 自身不会再做 3 次 retry，registry 会进入 cooldown，批量推荐 / discovery 路径也会跳过逐条 fallback，避免余额不足时继续制造大量必失败请求和日志。
- **Discovery 查询生成降本**：旧 B 站 `SearchStrategy` query 生成按画像 digest + pool hints 缓存；`ExploreStrategy` domain 生成改为短 JSON（只含 `domain/novelty_level/queries`，`max_tokens=2048`）并按画像 + covered topic groups 缓存；统一 `KeywordPlanner` 的 merged keyword 成功结果按画像 digest + 平台需求块 + 池子避让提示复用到 `[discovery].plan_ttl_hours`。真实环境验证发现 query/domain 生成仍会被完整画像和 thinking 放大，因此这些生成 caller 现在统一使用稳定 compact profile summary，flat `interests` 保留前 64 个，关闭额外 core memory 注入，并对 `search/explore` 关闭 DeepSeek thinking。后台 refresh 也改为约 90% 可换池低水位才跑 discovery，小缺口 B 站补货先只给 `search + related_chain` 预算，延后 `trending/explore`，避免几个库存缺口触发全套 planner/search/explore/trending。
- **KeywordPlanner 合并 explore 方向生成**：当 `explore_refresh_hours` 已到期或距到期不足一个 refresh tick，且 B 站平台族仍有补货空间时，统一关键词 planner 会在同一轮 merged keyword LLM 调用里追加 `explore_domains` block；返回的探索 query 写入 B 站 `discovery_keywords(keyword_kind="explore")` query cache，成功插入后推进 `last_explore_refresh_at`。`ExploreStrategy` 在统一 planner 开启时会从这个 explore 候选池 claim query 搜索，池为空则本轮跳过，不再单独触发 `discovery.explore.queries`；普通 B 站 search 仍只消费 `keyword_kind="regular"`，且 claim / history / recycle 默认都按 `keyword_kind` 隔离。
- **Trending rids 改为零 token 本地轮转**：`TrendingStrategy` 不再为选择 B 站排行榜分区调用 `discovery.trending.rids` LLM。现在固定抓 `rid=0` 全站榜，其余非 0 分区按 `profile_kw_digest + cycle + rid` 确定性洗牌，每轮最多取 `max_related_rids` 个，覆盖完一轮后再重新洗牌；个性化筛选留给后续内容 evaluator。
- **Discovery evaluator 去掉 text-first 重复正文**：知乎 / X 等文字优先来源如果 `description` 与 `body_text` 来自同一段文本，`discovery.evaluate_batch` 和单条 fallback 评估 prompt 会省略重复的 `description`，只保留 `body_text` 作为正文输入，降低旧知乎候选里摘要 / 正文重复造成的 prompt 膨胀。
- **Discovery interest 丰富度保护**：query / domain / keyword planner 的 compact profile summary 不再只是截取权重前 64 个兴趣；现在先取最多 128 个 interest 候选，再用 cache-only embedding 做 MMR 风格选择，在保留强兴趣的同时覆盖更多语义簇，并对贴近 `disliked_topics` 的 interest 降权。`disliked_topics` 自身也用同一缓存向量做多样性去重。真实 embedding cache 命中时，选择器预计算 dislike 相似度并增量维护“距已选最近相似度”，避免 prompt 构建阶段重复计算大量 bge-m3 cosine。没有 cached embedding 时保持原权重顺序，不新增热路径 embedding 调用。
- **推荐出口增加 dislike 硬过滤兜底**：`RecommendationEngine.serve()` 从 discovery pool 读出候选后，会按当前 `profile.preferences.disliked_topics` 再过滤一次；主题字段精确命中，或标题 / 标签 / 简介 / 作者 / 短正文包含避雷 term 的候选不会进入排序，覆盖异步清池尚未完成或清池失败的窗口。
- **Delight Score 复用 Evo 结果并清理旧 LLM 入口**：`precompute_delight_scores()` 不再为惊喜候选额外调用 Delight LLM；该版开始复用 Evo 已写入 `content_cache` 的 `relevance_score`，并曾临时在正式 copy 缺失时 fallback 到 `relevance_reason/topic`。后续 copy-readiness gate 已取代该 fallback：当前高分候选必须同时具备 `pool_expression / pool_topic_label`，才会原子同步为 `delight_reason / delight_hook`；evaluator reason/topic 永不展示。旧 `LLMDelightScorer`、Delight prompt builder 和 LLMService 中的 Delight caller 路由特例继续保持移除。
- **统一关键词 planner 切断 B 站旧搜索词 LLM 兜底**：`[discovery].unified_keyword_planner_enabled=true` 时，B 站主 refresh 若暂时 claim 不到 `discovery_keywords` 里的 pending 词，会只移除本轮 `search` 子策略并保留 `related_chain/trending/explore`，不再把 `queries=None` 传给 `SearchStrategy` 触发 `discovery.search.queries`，避免 planner 与旧搜索词生成同时烧 token。
- **插件与桌面安装包同步发布**：浏览器插件版本提升到 `extension-v0.3.99`，用于 GitHub Release 与 Chrome Web Store 同步分发；桌面安装包提升到 `desktop-v0.3.148`，让冻结包用户直接获得本轮 LLM 费用控制、dislike 兜底和关键词 planner 修复。
- **插件连接空态实时同步**：side panel 首次打开时如果 `/api/ping` 瞬时失败但 `/api/runtime-stream` 随后连上，现在会立刻把推荐页从“后端还没开张”离线空态切回在线刷新流程，不再只更新顶部“已连接”徽标；popup 离线期间会每 1 秒轻量重探测 `/api/ping`，runtime-stream 自身也改为固定 1 秒重连，后端启动后自动更新徽标并刷新推荐。
- **插件 Release 缺 AMO 密钥不再阻断**：`release-extension.yml` 现在会先探测 Firefox AMO 签名凭证；只有 `FIREFOX_SIGNING_ENABLED=true` 且 `AMO_JWT_ISSUER` / `AMO_JWT_SECRET` 同时存在时才要求 signed XPI，否则仍发布 Chrome / Edge zip 与 Firefox 临时加载 zip，避免未配置 Firefox 签名密钥时阻断插件包发版。
- **画像增量回填增加并发 claim 保护**：`/api/events` 的 `last_profile_pipeline_event_id` backfill 现在有进程内 single-flight 保护；当前一批旧 pending 行正在喂给 `ProfileUpdatePipeline` 时，并发事件请求会跳过重复 backfill，只处理自身 accepted 事件，避免同一批 200 条画像信号被重复送进 `soul.preference.chunk`。
- **画像编辑支持二级兴趣**：`GET /api/profile/edit-state` 现在会返回兴趣树的 `specific_edits` 痕迹；插件 side panel、移动 Web 和桌面 Web 的画像编辑面板会按 `domain -> specifics` 渲染，新增 / 删除二级兴趣时向 `/api/profile/edit` 带 `parent`，不再只能编辑一级兴趣域；新增后立即删除的二级兴趣会归约为空覆盖，不再留下错误的已编辑状态。
- **画像编辑态层级样式优化**：桌面 Web 的兴趣编辑树增加一级 domain 分组左侧层级线与二级 specifics 缩进分隔；插件 side panel 使用同一层级语义但收紧间距、字号和添加按钮 reset，避免二级兴趣编辑态挤成一片或露出浏览器默认按钮样式。
- **初始化 chunk 顺带生成临时觉察 / 洞察上下文**：`soul.preference.chunk` 的结构化输出现在可包含 `awareness_candidates` / `insight_candidates`；后端会去重合并后只作为本次 `soul.profile_build` 的 prompt 上下文，不写入长期 `awareness.json` / `insight.json`，让初始人格画像在首次生成时就能利用每个 chunk 提炼出的观察和假设。

## v0.3.147 / extension v0.3.98 / desktop v0.3.147: PC Web 正向反馈与探针原地聊天（2026-06-26）

后端源码走 `backend-v0.3.147`，浏览器插件走 `extension-v0.3.98`，桌面安装包走 `desktop-v0.3.147`。

- **PC Web 平台源状态与 Cookie 展示优化**：设置页“平台源”现在把“来源开关是否进入调度”和“Cookie / 令牌 / 插件任务是否可用”拆成两个 badge；知乎 `pending/unverified` 显示为“状态待验证”，修改来源开关但未保存时会标注“保存后生效”。新增 `/api/sources/credentials`，PC Web 会把 B 站 / 抖音 / X 当前 Cookie 和小红书最近 `xsec_token` 放在默认折叠的只读面板里；下方 Cookie 输入框仅用于覆盖，留空保存不会覆盖，只有主动粘贴新 Cookie 时才提交。
- **PC Web 推荐反馈保留正向内容**：推荐卡点赞和“聊一聊”提交后不再从当前列表消失，点赞按钮会显示已按下状态；只有“不感兴趣”和“忽略”这类负向 / 移除型反馈继续延迟淡出当前卡片。桌面端推荐加载过滤同步只隐藏负向反馈，和移动 Web 的反馈行为保持一致。
- **PC Web Inbox 探针支持原地聊天**：消息抽屉里的兴趣 / 挑战 / 避雷探针点击“多聊聊”时不再切到画像聊天页，而是在当前卡片内展开输入框并通过 `/api/chat/turns` 提交上下文聊天，回复 / 错误状态直接显示在卡片里。
- **插件维护包同步发布**：浏览器插件版本提升到 `extension-v0.3.98`，用于 GitHub Release 与 Chrome Web Store 同步分发；本次主要同步当前后端 / Web 修复后的聚合版本号，插件功能代码与 `extension-v0.3.97` 保持一致。
- **桌面安装包同步发布**：桌面安装包提升到 `desktop-v0.3.147`，让冻结包用户直接获得本轮 PC Web 设置页、推荐反馈和 Inbox 探针原地聊天修复。
- **聚合 Release 自动清理旧包资产**：`openbiliclaw-v*` 聚合页同步新插件 / 桌面安装包前会先删除旧版本 `.zip` / `.dmg` / `.exe` 包类资产，避免同一个最新 release 同时展示上一版下载包。
- **Firefox 签名 XPI 发布链路**：用户反馈 Firefox 直接安装 `openbiliclaw-extension-v*-firefox.zip` 会提示“未通过验证”。插件发布链路新增 AMO unlisted 签名脚本与 release workflow 上传，默认生成 `openbiliclaw-extension-v*-firefox.xpi` 供普通 Firefox 持久安装；`-firefox.zip` 保留为未签名开发包，仅用于 `about:debugging` 临时加载或 AMO 签名输入。
- **CI Web E2E 避开 runner 失效 apt 源**：`Web guided-init E2E` 在安装 Playwright Chromium 依赖前会清理 GitHub runner 上可能返回 403 的 Microsoft / azure-cli apt 源，避免 `python -m playwright install --with-deps chromium` 在 apt update 阶段被外部源拖失败。
- **候选评估限流不再误拒绝整批候选**：真实 SQLite + runtime drain E2E 复现 LLM 429 后，发现 batch evaluator 曾把 provider rate limit 转成全 0 分，导致 `discovery_candidates` 直接进入 `rejected_low_score`。现在 rate-limit / cooldown 会作为 transient failure 向上传递，`DiscoveryCandidatePipeline` 释放 claim 回 `pending_eval`，模型恢复后继续评估原候选。
- **画像上下文 LLM 调用缓存前缀稳定**：`PreferenceAnalyzer` 的单批 / 分片结构化 LLM 调用现在会在 `LLMService` 支持时关闭额外 core memory 注入；事件批次和 existing preference 仍在 user prompt 中完整传递，但 system prompt 不再拼入动态画像片段，提升 `soul.preference.chunk` 这类初始化高频调用的 provider prompt-cache 命中率。同一策略也扩展到 discovery 单条 fallback、推荐池分类、delight 批量评分、跨平台关键词生成、awareness / insight / speculation / profile build 等已自带 `profile_summary` / `soul_profile` / `preference_summary` 的结构化调用，并用共享 helper 兼容不支持 `inject_core_memory` 的测试 stub 或旧服务对象。
- **初始画像生成 prompt 稳定块前置**：`build_soul_profile_prompt()` 的 system prompt 改为完全静态，动态 `tone_profile` / 来源基调移动到 user prompt 首部；user prompt 顺序调整为 `<tone_profile>` → `<preference_summary>` → `<recent_awareness>` → `<active_insights>` → `<history_summary>`，并对 JSON 输入使用 `sort_keys=True`，避免巨大的历史摘要打断 provider prompt-cache 前缀。
- **画像按层缓存输入扩展到推荐与关键词链路**：共享 `profile_prompt_layers()` 会把 `profile_summary` 拆成 `<profile_core>` / `<profile_life_context>` / `<profile_interests>` / `<profile_style_context>` / `<profile_recent_context>` 五层并按稳定性排序。`discovery.evaluate_batch`、推荐池分类、批量 / 单条推荐文案、delight 批量评分 / 备用理由、统一关键词 planner 都通过 `PromptLayerRenderCache` 按层 digest 复用渲染后的 prompt block：画像核心没变时保持 provider 可见前缀 byte-stable，近期觉察或洞察变化时只更新后置 recent 层，不牺牲各环节可见的完整画像。

## v0.3.146 / extension v0.3.97 / desktop v0.3.146: 知乎长 ID 链接保真（2026-06-26）

后端源码走 `backend-v0.3.146`，浏览器插件走 `extension-v0.3.97`，桌面安装包走 `desktop-v0.3.146`。

- **知乎长 ID 链接不再被 JS 舍入**：插件知乎 task executor 对站内 API 响应做 lossless JSON 解析，把超过 `Number.MAX_SAFE_INTEGER` 的裸整数先转成字符串；归一化时也会优先从 URL 字符串解析 question / answer / article ID，修复 19 位 question id 被舍入成错误知乎链接的问题。真实后端 + 已连接浏览器插件 E2E 覆盖 `discover-zhihu-hot` 和指定 `2053435015258804659` 的 `discover-zhihu-related`，确认入库 URL 不再出现舍入后的 `2053435015258804700`。

## v0.3.145 / extension v0.3.96 / desktop v0.3.145.1: Eval 缓存与推荐理由并发优化（2026-06-26）

后端源码走 `backend-v0.3.145`，浏览器插件走 `extension-v0.3.96`，桌面安装包走 `desktop-v0.3.145.1`。

- **抖音 / YouTube init 提问默认改为跳过**：交互式 `openbiliclaw init` 的“加入抖音数据?”和“加入 YouTube 数据?”现在与小红书一致默认 No，避免回车误触发需要登录浏览器前台 tab 的 bootstrap；显式启用仍使用 `--yes-douyin` / `--yes-youtube` 或回答 yes。
- **Evo 前供给改为按水位补肉**：`DiscoveryCandidatePipeline.ensure_pending_supply()` 会按 `pending_eval + evaluating` 水位循环生产 raw candidates，直到接近本轮 evaluator batch、池子已满、没有新候选或达到尝试 / 时间预算；refresh path 优先调用该 supply loop，不再只跑一次 discover 后插入几个算几个。
- **Evo 首批评估强制使用批量下限**：API runtime 配置的 `min_eval_batch_size=8` 现在会同时约束 refresh 的 supply target、策略预算和 drain claim size；即使池子只差 1-7 条，首次 evaluator 也会先攒到 8 条或等待超时，不再因缺口算法把 first drain 压成 6 条。
- **入待评估池前过滤历史重复**：候选入库前会先过滤同批重复、历史 `discovery_candidates` 任意状态和已进入 `content_cache` 的 BVID/content_id，减少重复 discovery 占住 raw 前排后被 `INSERT OR IGNORE` 静默吞掉导致 Evo 只拿到 1-3 条。
- **热重载取消不再卡住 evaluating**：真实端到端测试发现插件 cookie 同步触发 hot-reload 时，正在跑的 Evo batch 可能在模型返回后被取消，导致候选停在 `evaluating`；pipeline 现在捕获 `CancelledError` 并即时释放 claim 回 `pending_eval`，后续 drain 可继续处理。
- **候选 eval 缓存命中优化**：批量 evaluator 的本地 cache key 改为候选身份 + full profile digest + negative_examples digest，不再被 Python profile 对象 id 或无关事件水位打穿；`discovery.evaluate_batch` 调用 LLMService 时会在支持的 provider/service 路径上关闭额外 core memory 注入，复用 prompt 内的完整结构化 profile，提升 provider prompt-cache 前缀稳定性。
- **推荐理由生成缓存前缀保护**：推荐池批量文案、单条实时文案和备用 delight reason 调用 LLMService 时同样在支持路径上关闭额外 core memory 注入；推荐 prompt 仍保留完整结构化 profile，只去掉重复拼接，减少 token 并让 `recommendation.write_expression` / `recommendation.expression` 的 provider prompt-cache 前缀更稳定。
- **eval / 推荐理由生成默认双 worker**：统一候选 evaluator 单次 drain 默认最多领取两个 batch 的候选，并由 `evaluate_content_batch()` 以 2 个 worker 跑 LLM batch；推荐池文案 `_drain_expression_copy()` 也改为默认 2 个 worker。外层 drain / expression lock 仍串行化多入口，claim size 仍受 evaluator hard cap 约束，取消时不吞 `CancelledError`。
- **长上下文 eval 默认大 batch，推荐理由保守 30**：文本 `discovery.evaluate_batch` 默认 batch size 从 30 提到 45；周期 candidate-eval loop 未显式传参时也按 45 drain。多模态 eval 继续使用独立小 batch。真实 provider 并发测试显示 `recommendation.write_expression` 45 条偶发 JSON 解析失败，因此推荐文案默认 batch 保持 30；批量解析失败时仍会先在同一 worker 内递归拆半重试，provider 限流仍直接留空等待下一轮，避免并发或重试倍增。
- **macOS DMG 加入首次打开指引**：未签名 / 未公证的实验桌面包现在会在 DMG 内放入 `首次打开说明 First Launch.html` 和可见安装提示图，Release notes 与 README 同步说明右键 / Control-click 打开和 Privacy & Security fallback，降低用户找不到“仍要打开”的概率。
- **搜索关键词 claim 接入供给水位**：B 站 search 关键词只有在待评估水位不足时才 claim；如果 `pending_eval + evaluating` 已经足够，本轮不会空 claim 后又因 supply loop 不抓内容而误标 failed。
- **相关推荐 seed 优先正反馈**：`RelatedChainStrategy` 的事件种子现在优先使用 `favorite` / `like` / `coin` / `share` / positive feedback，普通 `view` 降为 fallback，减少 related_chain 从弱浏览信号继续挖窄内容圈。

## v0.3.143 / extension v0.3.94 / desktop v0.3.143: 候选评估蓄水与补池诊断（2026-06-25）

后端源码走 `backend-v0.3.143`，浏览器插件沿用 `extension-v0.3.94`，桌面安装包走 `desktop-v0.3.143`。

- **候选评估先蓄 batch**：API daemon runtime 的 `DiscoveryCandidatePipeline` 现在少于 8 条 `pending_eval` 不会立即跑 LLM，最多等待 120 秒后才放行小批次，避免 1-3 条候选也消耗一整份 20k+ token 画像 prompt。周期 drain 日志会把等待状态标成 `reason=batch_waiting`。
- **评估 prompt 输入瘦身**：`ContentDiscoveryEngine.evaluate_content_batch()` 在构建 batch prompt 前会压缩画像摘要，只保留高权重兴趣 / 领域、最新 awareness / insight 和完整 `disliked_topics`，减少 evaluator 的固定输入 token，同时保留关键避雷和近期语境。
- **低可用池不再被 source overflow 压掉**：`_enforce_pool_cap()` 在 `pool_available < pool_target_count` 时跳过 `trim_pool_source_overflow()`，避免 raw/source 配额把当前可用候选继续 suppress；总 raw ceiling 仍由 `trim_pool_to_target_count()` 收敛。
- **空补货计划可诊断**：`_build_refresh_plan()` 在池子低于 target 但 plan 为空时会输出 `pool_available/raw/pending/source_available/source_raw/source_targets/raw_targets/requested_by_source`，方便直接定位是来源配额、raw headroom、非 B 站 producer 还是其它 gating 导致不补。
- **减少重复 discovery 导致的小批 eval**：API runtime 的主 discovery raw 生产改为 4 倍 oversample，并同步放大 strategy limits；重复候选仍由 `candidate_key` 去重，但新候选更容易把 `pending_eval` 攒到有效 batch。
- **画像整理日志区分 run 与 batch**：`ProfileConsolidator` 每次逻辑运行结束会输出一条 `profile consolidation run completed` 汇总，包含 `run_id`、候选簇数、LLM batch 数、合并 / 归档数量和前后库存，避免把同一轮拆批 LLM 调用误判为短时间重复合并。
- **OpenAI SDK DEBUG 降噪**：全局 logging 初始化现在把 `openai` / `openai._base_client` 提升到 WARNING，避免 `logging.file_level=DEBUG` 时把完整 LLM prompt / 用户画像写进文件日志；业务侧 `[llm-cost]` 与模块 INFO 日志不受影响。

## v0.3.142 / extension v0.3.94 / desktop v0.3.142: 知乎后台 discovery 与发布包同步（2026-06-25）

后端源码走 `backend-v0.3.142`，浏览器插件走 `extension-v0.3.94`，桌面安装包走 `desktop-v0.3.142`。

- **知乎 discovery 不再抢前台**：浏览器插件只在 `bootstrap_events` 初始化 / 事件 smoke 时打开前台知乎 tab，便于用户确认浏览 / 收藏 / 点赞收藏信息收集；search / hot / feed / creator / related discovery 改用后台任务 tab，后台补池不会打断当前浏览焦点。
- **同步知乎来源对外定位**：GitHub About、包描述、README 中英文架构摘录、`docs/spec.md` 与 discovery 模块文档统一把知乎列为已落地跨平台来源，避免仍被描述成 B 站单源工具。
- **发布插件与桌面安装包**：插件版本提升到 `extension-v0.3.94`，后端 / 桌面安装包版本提升到 `desktop-v0.3.142`，用于 GitHub Release 聚合页分发。
- **真实环境验证**：本地真实 API + 已连接浏览器插件完成 `discover-zhihu-hot --limit 3` E2E，扩展任务完成并写入 3 条 `zhihu-hot` 候选；后台 tab 分支配套单测覆盖 `bootstrap_events` 前台、discovery 后台。

## v0.3.141 / extension v0.3.93 / desktop v0.3.140: 推荐池补货死锁修复（2026-06-25）

后端源码走 `backend-v0.3.141`，浏览器插件走 `extension-v0.3.93`，桌面安装包暂沿用 `desktop-v0.3.140`。

- **修复 raw ceiling 误停补货**：当 `pool_available_count` 低于 `pool_target_count`、但 raw material 已达到 ceiling 时，`ContinuousRefreshController` 不再把 source deficit 算成 0；Search / producer 会继续补足可用池，raw ceiling 仍由 `_enforce_pool_cap()` 和 post-refresh trim 负责收敛，避免 pending keywords 长期不被消费、日志只剩 `enforce_pool_cap` / `candidate eval drain no_pending`。
- **同步发布插件维护包**：浏览器插件版本提升到 `extension-v0.3.93`，用于 GitHub Release 和 Chrome Web Store 包同步分发；插件功能代码与 `v0.3.92` 保持一致。
- **同步知乎来源对外定位文档**：GitHub About、包描述、README 中英文架构摘录、`docs/spec.md` 和 discovery 模块文档统一把知乎列为已落地跨平台来源，避免仍被描述成 B 站单源工具。
- **知乎 discovery 改为后台任务 tab**：插件仍用前台 tab 执行 `bootstrap_events` 初始化 / 事件 smoke，便于用户感知浏览 / 收藏 / 点赞收藏信息收集；search / hot / feed / creator / related discovery 则改为后台 tab，避免后台补池打断用户当前浏览焦点。

## v0.3.140 / extension v0.3.92 / desktop v0.3.140: 知乎多源接入与插件发现（2026-06-24）

后端源码走 `backend-v0.3.140`，浏览器插件走 `extension-v0.3.92`，桌面安装包走 `desktop-v0.3.140`。

- **新增知乎事件爬取 smoke 链路**：`openbiliclaw fetch-zhihu` 会通过后端 `zhihu_tasks` 队列与浏览器插件前台知乎 tab 拉取最近浏览记录、收藏夹条目和个人动态里的点赞 / 收藏动作。插件会优先用 `/api/v4/me` 自动识别当前知乎用户，`--profile-slug` 仅作为手动覆盖；收藏夹改走当前可用的 favlists API，旧 `/collections/mine` HTML 路径只作为 fallback；动态点赞和动态收藏各自独立使用单分支上限，不共享额度。插件新增知乎 `PlatformAdapter`、content task executor、后台 dispatcher 和 manifest 权限；后端新增 `/api/sources/zhihu/next-task` / `task-result` / `kick`。该命令只转换并打印统一事件计数，不写入 memory、不触发画像初始化或增量画像更新；任务 tab 带 `openbiliclaw_zhihu_task` 标记，content script 在该模式下只运行 executor，不启动普通行为采集，避免 smoke 拉取污染 `/api/events`。
- **新增知乎搜索 discovery 链路**：`openbiliclaw discover-zhihu <keyword...>` 会入队 `zhihu_tasks(type="search")`，用已登录浏览器插件拉取 `zhihu_search` 候选并写入 `discovery_candidates(pending_eval)`，不写 memory、不触发画像初始化。runtime 新增 `ZhihuDiscoveryProducer`，在 `[sources.zhihu].enabled=true` 且候选池 Zhihu 低于 `[scheduler.pool_source_shares].zhihu` 配额时按统一关键词 planner / 画像 fallback 入队搜索任务；`DiscoveredContent` / 候选池 / source policy / refresh controller / `/api/config` / `/api/sources/status` / 插件设置页 / 桌面 Web 设置页都纳入 `source_platform="zhihu"`。默认保存配比改为 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 = `5 / 1 / 1 / 1 / 1 / 1`，未启用的平台仍不会占 runtime quota。
- **扩展知乎 discovery 为五路来源**：在搜索之外新增 `hot` / `feed` / `creator` / `related` 任务类型，分别回传 `zhihu_hot` / `zhihu_feed` / `zhihu_creator` / `zhihu_related` 候选并以 `zhihu-hot` / `zhihu-feed` / `zhihu-creator` / `zhihu-related` 写入统一待评估池。CLI 新增 `discover-zhihu-hot` / `discover-zhihu-feed` / `discover-zhihu-creator` / `discover-zhihu-related` 真实插件 smoke 命令；配置新增 `[sources.zhihu].source_modes` 和四个独立 daily budget。
- **知乎接入正式 discover 与配置页分支开关**：`openbiliclaw discover --source zhihu` 不再只提示跳转 smoke 命令，而是复用 runtime `ZhihuDiscoveryProducer` 按配置的 `source_modes` 入队真实插件任务并接入统一 candidate evaluator。插件 side panel 与桌面 Web 配置页新增 search / hot / feed / creator / related 五个显式勾选项，保存时直接写回 `[sources.zhihu].source_modes`。
- **知乎推荐卡三端显示补齐**：桌面 Web、移动 Web 与插件 side panel 的推荐卡现在都能按 `source_platform="zhihu"` 显示知乎来源；知乎回答 / 文章 / 问题默认走文字卡，不再在移动 Web 缺链接时误构造 B 站 URL，并为三端补齐知乎来源徽标 / 封面背景样式。
- **知乎补齐初始化和状态闭环**：CLI、`/setup/`、桌面 Web 和插件初始化 CTA 都新增知乎来源选择；`init --yes-zhihu` 会复用 `zhihu_tasks(type="bootstrap_events")`，把最近浏览 / 收藏夹 / 动态点赞 / 动态收藏转换为首轮画像事件，并持久化 `[sources.zhihu].enabled=true`。`fetch-zhihu` 仍保持独立 smoke，不写 memory；`GET /api/sources/status` 的知乎状态改为根据最近任务结果本地判定 `unverified` / `ready` / `missing` / `partial`，不再固定显示 `no_auth`。`ZhihuDiscoveryProducer` 在 creator / related 没有历史种子时会用同轮 search / hot / feed 返回的作者页和内容 URL 兜底，冷启动也能跑全五个分支。
- **知乎事件回填补齐 memory / 画像路径**：`fetch-zhihu` 新增 `--write-memory` 和 `--rebuild-profile`。默认仍只做真实插件 smoke；`--write-memory` 会把本次抓到的知乎浏览 / 收藏 / 点赞事件去重后写入 memory，`--rebuild-profile` 隐含写入并触发真实 LLM 画像重建。`/api/sources/zhihu/task-result` 对 payload 显式带 `profile_update=true` 的 `bootstrap_events` 任务会像其它平台一样把新增事件传播到 memory，并在 profile 已存在时进入 `ProfileUpdatePipeline`；普通 smoke 任务保持不污染画像。
- **知乎来源比例升级兼容**：旧 `config.toml` 若已有 `[scheduler.pool_source_shares]` 但缺少 `zhihu`，配置加载和运行时 source policy 会自动补默认 `zhihu=1`；配置页保存 `pool_source_shares.zhihu` 后，启用知乎时会进入有效平台配比，关闭知乎时仍保留配置值但不占 runtime quota。
- **画像偏好分析补齐网页长文本拒答兜底**：真实知乎画像重建时发现 DeepSeek 偶发把含长回答摘要的 preference chunk 拒答成非 JSON。`PreferenceAnalyzer` 的 chunked 路径现在先把可恢复的非 JSON 当作重试信号而不是直接 ERROR；单条事件仍失败时会去掉长 `context`，保留 title / URL / source metadata 做一次安全压缩重试，避免整条知乎浏览 / 收藏 / 点赞信号被丢弃。新增回归测试覆盖“原始 context 被拒答、压缩后成功提取兴趣”的场景。
- **推荐池消费后库存状态实时收敛**：`GET /api/recommendations` 首次从候选池补历史、`/api/recommendations/reshuffle` 和 `/api/recommendations/append` 消费可换内容后，会立即重新读取 runtime 池子口径并广播 `refresh.pool_updated`，避免其它已打开客户端继续显示旧的“可换”数量。插件 side panel 和移动 Web 收到该事件时同步刷新底部可换提示 / 空态文案但不重拉推荐列表；桌面 Web 首屏在推荐 bootstrap 后会再读一次 `/api/runtime-status`，并把左侧标签改为“当前可换库存 / 上次成功补货”，减少“当前库存”和“上一轮补货结果”混读。

## v0.3.139 / extension v0.3.91 / desktop v0.3.139: 更新检查限流兜底与知乎 smoke（2026-06-24）

后端源码走 `backend-v0.3.139`，浏览器插件走 `extension-v0.3.91`，桌面安装包走 `desktop-v0.3.139`。

- **检查更新区分并绕过 GitHub API 限流**：后端自动更新查询 GitHub tags 时会把 REST API quota 耗尽的 403/429 识别出来，并优先用 GitHub tags Atom feed 兜底继续选择 `backend-v*` / `desktop-v*`；兜底也失败时才稳定上报 `github_rate_limited`，不再误报 `github_unreachable`。插件 side panel 和桌面 Web 设置页同步显示「GitHub API 限流，请稍后再试」；安装包模式下插件也会隐藏“立即应用”，改为提示下载新版安装包。
- **画像整理维护 active 库存上限**：`ProfileConsolidator` 新增 active likes 上限 / 整理水位 / 自动归档配置。画像整理在 active likes 超过上限时不再因 digest 未变 clean-skip，而是临时开 full boundary；合并后仍超上限时，把低权重且非用户保护的长尾兴趣移入 `archived_interests`，后续新信号命中同名同类会自动复活，run record 可整体回滚 active / archived inventory。
- **超上限时动态放宽合并候选召回**：当 active likes 超过上限时，likes embedding 聚类阈值会按 `upper -> soft` 水位压力逐步从默认 `0.85` 降低，最低默认 `0.75`，让 LLM 看到更多可压缩候选簇；LLM 裁决、canonical 防泛化和归档兜底仍负责防止过度合并。CLI 与 run record 会记录本轮实际 likes 阈值。
- **画像合并产出代表性 item**：LLM 画像整理的 canonical 不再偏向机械保留某个旧兴趣名；当多个成员分别覆盖合并概念的一部分时，prompt 要求产出更能代表整组的具体 item 名。合并后的 active interest 会把原成员词写入 `aliases`，后续增量偏好命中 alias 时会强化 canonical item 而不是重新长出重复兴趣；同时新增 likes 侧过泛 canonical 拒绝，避免把具体兴趣压成裸大类。
- **新增知乎事件爬取 smoke 链路**：`openbiliclaw fetch-zhihu` 会通过后端 `zhihu_tasks` 队列与浏览器插件前台知乎 tab 拉取最近浏览记录、收藏夹条目，并可用 `--profile-slug` 补个人动态里的点赞 / 收藏动作。插件新增知乎 `PlatformAdapter`、content task executor、后台 dispatcher 和 manifest 权限；后端新增 `/api/sources/zhihu/next-task` / `task-result` / `kick`。该命令只转换并打印统一事件计数，不写入 memory、不触发画像初始化或增量画像更新，便于先做真实端到端来源验证。
- **跨平台事件强度进入偏好分析**：统一事件构造会为缺失 `metadata.signal_strength` 的行为补兜底强度，B 站初始化 / 账号同步、小红书、抖音、YouTube、X、知乎等来源都能用同一套“证据强度”语义进入 PreferenceAnalyzer；平台自带的强度值优先保留。偏好分析 prompt 明确 `signal_strength` 不是最终兴趣权重，负向反馈 / dislike / thumbs_down / negative satisfaction 仍优先进入避让或降权。
- **推荐卡反馈按强信号处理**：推荐卡 `comment` 反馈的 `signal_strength` 从 `0.6` 提到 `0.8`，`dismiss` 从 `0.4` 提到 `0.5`；`like` / `dislike` 继续保持 `1.0`。端到端覆盖 `/api/feedback` -> `MemoryManager` -> SQLite 事件入库，确保真实反馈卡片进入画像链路时带正确强度。
- **推荐反馈画像学习防并发重放**：`/api/feedback` 现在通过 `FeedbackBatchScheduler` 做 5 秒 debounce / coalesce，burst 内多条反馈只触发一次画像批学习；`SoulEngine.process_feedback_batch_if_needed()` 增加 single-flight 锁，已有批处理运行时不再用旧 cursor 并发重复分析当前全部未处理反馈。反馈批处理改用 `query_events_since()` 按 `id ASC` 读取 cursor 后的全部新增 feedback，避免大积压时 newest-first `limit=500` 跳过较早未处理事件。传给 `PreferenceAnalyzer` 前还会瘦身 feedback 事件 metadata，避免扩展原始 `targetText/raw_context` 等大字段进入 LLM prompt。
- **偏好分析 chunk 调度分批推进**：`PreferenceAnalyzer` 初始化大批量事件时不再一次性 fan-out 全部 chunk，而是每批最多推进 16 个 chunk，处理完再进入下一批；默认粗分片大小收口为 `DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE=200`，即本地批次最多推进约 3200 条事件后再进入下一批。`LLMService` 默认并发保持不变，避免拉全量历史时产生无界 prompt 任务和等待队列。
- **补充 PR #69 贡献者致谢**：README 中英文致谢列表新增 [@tangle111-design](https://github.com/tangle111-design)，记录其在 `style_key` 观看模式、推荐语气、B 站初始化和 LLM / 画像流程方面的探索贡献。
- **X 发现依赖纳入默认安装**：`twitter-cli>=0.8.5` 从可选 extra 提升为默认运行时依赖，普通包安装、AI 默认安装和开发安装都会带上 `twitter_cli` import 包，避免启用 `[sources.twitter]` 后后台 producer 才报 `No module named 'twitter_cli'`。`openbiliclaw[x]` 仍保留为兼容旧脚本的别名。
- **普通行为事件接入增量画像 pipeline**：`POST /api/events` 现在只把 accepted 的普通浏览器行为事件喂给 `ProfileUpdatePipeline.ingest_batch()`，并在 pipeline ingestion 后通过 `request_replenishment(reason="event_ingest")` 排队补货需求；rejected / not_initialized 事件不进入画像 pipeline。为覆盖旧版本已经落库但停在 discovery 水位后的行为，API 会用独立 `last_profile_pipeline_event_id` 先补喂这批 pending 事件，且不推进 discovery 的 `last_processed_event_id`。这样插件日常捕捉的点击、搜索、收藏等行为不再只落 memory 和 discovery 水位。
- **补货入口收束到定时 / 手动两类执行路径**：新增 `ContinuousRefreshController.request_replenishment(reason, force=False)` 作为统一入口；普通事件和反馈只记录 reason，等待定时 `refresh_if_needed()` 或用户刷新后的低库存检查统一补货。init-completed、用户手动刷新和推荐刷新后低库存路径使用 `force=True` 触发手动补货，并会消费之前排队的 reason，避免普通事件入口分散直接执行 discovery refresh。
- **pending 行为信号文案收口**：桌面 Web 和 `/api/activity-feed` 不再把 `pending_signal_events` 显示为“待处理行为信号”，统一改成“已记下 N 个新动作，下一轮补货会拿来参考”。该字段语义明确为 discovery refresh 游标后的新动作数量，不代表画像 pipeline backlog。

## v0.3.138 / extension v0.3.90 / desktop v0.3.138: macOS Ollama 动态库补齐（2026-06-23）

后端源码走 `backend-v0.3.138`，浏览器插件沿用 `extension-v0.3.90`，桌面安装包走 `desktop-v0.3.138`。

- **修复 v0.3.137 真实 DMG E2E 暴露的第二层 Ollama 缺包**：`v0.3.137` 已经不再缺 `llama-server`，但安装包内 `llama-server` 启动时仍会因 `libllama-server-impl.dylib` 等动态库未随包复制而让 `/api/embeddings` 返回 500。`packaging/build.py` 现在把官方 `Ollama.app/Contents/Resources` 视为一个 runtime 单元：除 `ollama` 外同时复制 `llama-server`、`llama-*`、`lib*.dylib`、`lib*.so` 和 `mlx_metal_*` 目录。
- **发布 workflow 增加动态库闸门**：`release-desktop.yml` 和手动 `build-installers.yml` 下载官方 `Ollama-darwin.zip` 后会检查 `libllama-server-impl.dylib` / `libggml.dylib`，避免再次生成“`/api/version` 正常、模型能下载、真实 embedding 才崩”的 macOS 安装包。
- **打包回归测试覆盖完整 runtime**：`tests/test_packaging_build.py` 现在断言 macOS onedir 与 `.app/Contents/Resources` 都包含关键动态库、`llama-quantize` 和 `mlx_metal_*` 目录，并在缺失关键 dylib 时直接拒绝构建。

## v0.3.137 / extension v0.3.90 / desktop v0.3.137: macOS 安装包 Ollama runtime 修复（2026-06-23）

后端源码走 `backend-v0.3.137`，浏览器插件沿用 `extension-v0.3.90`，桌面安装包走 `desktop-v0.3.137`。

- **macOS 安装包不再打进 Homebrew 半残 Ollama**：macOS `release-desktop.yml` 改为下载官方 `Ollama.app` runtime，并把 `Contents/Resources/ollama` 指给打包脚本；构建时会显式校验 `Contents/Resources/llama-server` 存在。`packaging/build.py` 现在在 Darwin bundle 中强制携带 `ollama + llama-server`，若只发现 Homebrew 风格的单独 `ollama` 主程序则直接失败，避免再次发布“`/api/version` 正常但 `/api/embeddings` 500: llama-server binary not found”的安装包。
- **初始化前真实确认 embedding 可用**：`/api/init-status` 新增 `prerequisites.embedding_required`；当 `[llm.embedding].provider` 已配置（安装包默认 `ollama`）时，`can_start` 和 `POST /api/init` 都必须等 `EmbeddingService.probe()` 完成真实向量请求才放行，失败时返回 `embedding_not_ready` 并回滚 init 预约。用户显式留空 provider 时仍允许降级初始化。桌面 `/setup` 和 `/web` 清单按该字段把向量模型显示为硬前置或可选项。
- **打包回归测试补齐**：`tests/test_packaging_build.py` 新增 macOS Ollama sidecar 拷贝 / 缺失拒绝 / release + 手动 installer workflow 官方 runtime 来源断言，守住后续桌面包 embedding 开箱即用承诺；`tests/test_api_app.py` 覆盖配置了 embedding 时 init 前硬拦、未配置时不拦。

## v0.3.136 / extension v0.3.90 / desktop v0.3.136: 候选 raw 评估独立 drain（2026-06-23）

后端源码走 `backend-v0.3.136`，浏览器插件走 `extension-v0.3.90`，桌面安装包走 `desktop-v0.3.136`。

- **pending raw 不再依赖 refresh plan 才能评估**：`ContinuousRefreshController.run_forever()` 新增 `_loop_candidate_eval()`，每个 refresh tick 独立 drain `discovery_candidates(pending_eval)`，并在 admission 后触发 `precompute_pool_copy()` 让候选进入真实可换池。refresh plan 发现新 raw 后仍会即时 eval，但 refresh path 和 periodic path 现在共用 `_discovery_drain_lock`，锁被占用时跳过本轮而不排队，避免两个入口并发评估同一批 raw。新增真实 SQLite + `DiscoveryCandidatePipeline` 端到端测试覆盖“pending raw -> eval -> content_cache -> pool copy -> 可换池”的完整链路。
- **B 站扩展搜索兜底吸收 content script 注入抖动**：真实浏览器 E2E 暴露出搜索页 `complete` 早于 content script listener 注册时会偶发 `sendMessage_failed`；`bili-task-dispatcher` 现在对 `BILI_TASK_EXECUTE` 做 8 秒短重试，避免把可恢复时序误判为任务失败。
- **真实浏览器 E2E 依赖补齐**：`.[dev]` 新增 `websockets`，小红书 browser E2E 的 backend / CDP URL 可通过环境变量覆盖，确保 9222 被占用时仍能用真实 Chrome 运行完整链路。

## v0.3.135 / extension v0.3.89 / desktop v0.3.135: 抖音 search discovery 真实召回修复（2026-06-21）

后端源码走 `backend-v0.3.135`，浏览器插件走 `extension-v0.3.89`，桌面安装包走 `desktop-v0.3.135`。

- **抖音 search discovery 恢复真实召回**：search 仍从抖音首页搜索框输入关键词并点击按钮提交，且继续用 `search_navigation_ok` 校验真实搜索结果路由；当页面自身 search fetch tap 与 DOM 解析都没有候选时，content script 会改用已登录页面的 MAIN-world search API bridge 兜底，避免当前抖音搜索页软空 / 响应时序变化导致 `dy_search=0`。真实环境 E2E 已重新验证 search / hot / feed 三个 discover 渠道均返回 3 条候选。

## v0.3.134 / extension v0.3.88: 初始化前事件入口收口（2026-06-21）

后端源码走 `backend-v0.3.134`，浏览器插件走 `extension-v0.3.88`，桌面安装包走 `desktop-v0.3.134`。

- **未初始化 activity feed 不再抢显示待处理信号**：`/api/activity-feed` 现在和推荐空态使用同一初始化优先级；在 `initialized=false` 且还没有推荐 / 可换池 / 补货产物时，`pending_signal_events` 只保留为后台事实，不再把 popup / Web 首屏文案改成“已经记下 N 个信号”，避免保存 LLM provider 后误导用户以为初始化已经开始。
- **初始化前普通行为事件不再入库**：`POST /api/events` 在 soul 画像明确未初始化时返回 `accepted=0` / `rejected.reason=not_initialized`，不写入 memory、不触发 `activity.added`、也不增加 `pending_signal_events`。首轮画像信号只由用户点击「开始初始化」后的 guided init 来源拉取；初始化任务自己的 `/api/sources/*/task-result` 仍按 init-owned 逻辑放行。
- **B 站收藏夹初始化按页补齐**：`get_favorites()` 不再固定只取收藏夹第一页 20 条；分页停止优先遵守 B 站返回的 `has_more`，覆盖第一页不足 20 条但仍有后续页的真实账号形态。初始化会把 `--bilibili-favorite-limit` 作为跨收藏夹总预算传入，单个收藏夹按页补齐到剩余预算。
- **B 站初始化默认信号上限调高**：首轮初始化默认导入的 B 站观看历史从 300 条提升到 500 条，收藏总预算从 300 条提升到 500 条；关注 UP 默认仍保持 100 人。

## v0.3.133 / extension v0.3.87: 推荐池 admission 统一收口（2026-06-21）

后端源码走 `backend-v0.3.133`，浏览器插件走 `extension-v0.3.87`，桌面安装包走 `desktop-v0.3.133`。

- **推荐池 admission 取消 observed 特权**：新增 `[discovery].admission_min_score=0.60` 作为普通统一入池最低分；B 站扩展搜索、小红书 observed 和其它插件 / 来源候选都必须先过 evaluator 分数门，普通策略 / producer 默认阈值也统一为 0.60。探索类策略可使用略低阈值鼓励新方向，但不再有平台 / observed 特权；数据库读取、suppressed 复活、delight 候选和 `/api/recommendations` 历史输出同步加低分过滤，并在初始化时压制旧低分 `content_cache` / `recommendations` 脏数据。
- **PC setup / Web 初始化完成态等首批内容池**：安装包 `/setup/` 和桌面 Web `/web` 不再只凭 `init-status.initialized=true` 就进入完成态；收到 `init_completed` 后会继续读取 `/api/runtime-status`，只有 `pool_available_count>0` 或已有推荐数时才算首轮初始化完成。画像已生成但首批内容还没入池时，PC 侧会停在“整理首轮内容池”进度态，和浏览器插件“有内容可刷后再进入推荐体验”的语义对齐。
- **首轮 discovery 冷启动多样性保护**：guided init 的空池首轮补货和统一 keyword planner 的空池首批跨平台关键词都会构造 `cold_start` pool snapshot，把画像里最高权重兴趣当作“软避让”而不是厌恶项，同时把次级兴趣 / 兴趣域作为 `prefer_axes` 注入搜索词 prompt；首批 query / keywords 保留少量强兴趣入口，但至少一半预算覆盖其它画像相关方向，降低各策略 / 各平台同时涌向单一高权重 topic 的概率。
- **Discovery batch evaluator 结构化输出更稳**：批量内容评估 prompt 改为顶层 JSON object + `results` 数组，和 OpenAI-compatible 的 `json_object` 模式一致；解析器同时兼容 `{"BVxxx": {"score": ...}}` 这类按内容 ID 映射的返回，并在降级逐条评估时记录异常类型与原因，便于定位 provider 偶发结构漂移。
- **B 站搜索插件兜底不再只等全局冷却**：单个 `v_voucher` 关键词耗尽仍不会触发 API 全局 cooldown、也不会让 explore 一起停摆，但会打开短期 DOM fallback 信号；扩展在线且 B 站池子低于配额时，runtime producer 可以立即入队浏览器真实搜索页任务补货。
- **PC Web 空推荐不再显示演示卡片**：桌面 Web `/web` 初始推荐列表改为空数组，且 `/api/recommendations` 返回空列表时会显式清空当前卡片，避免候选池为 0 时露出前端内置 demo 内容；插件 side panel 原本已使用空数组初始化，不受影响。

## v0.3.132 / extension v0.3.86: 初始化向导与推荐语气修复（2026-06-21）

后端源码走 `backend-v0.3.132`，浏览器插件走 `extension-v0.3.86`。桌面安装包未改动；如冻结包用户需要同步本次 Web / 后端修复，可后续单独打 `desktop-v0.3.132`。

- **图形化初始化来源勾选即生效**：`/setup/`、桌面 Web 和插件推荐 tab 不再把“小红书 / 抖音 / YouTube / X 已勾选但未在设置开启”当作启动前错误；显式 `sources` 现在是本轮 guided init 的 opt-in，并 best-effort 写回 `sources.<platform>.enabled=true`。前置清单同步显示“本次初始化来源”，避免首启默认勾选后仍报未开启。
- **`/setup/` 保存模型配置不再提前启动画像 / 探针**：安装包首启向导第一页把“模型名”移出高级折叠并自动填入推荐默认模型；点击“保存并继续”只保存 LLM/provider/model 并热重载组件，同时用 `suppress_background_llm_work=true` 暂停 post-reload speculator、画像/探针和补池后台工作。只有第二页选择来源并点击“开始初始化”后才真正进入四阶段 guided init，初始化终态后再恢复后台循环。
- **推荐表达语气固定跟随用户画像**：推荐文案不再因为内容 `style_key` 是日常、轻聊或审美浏览就把语气自动调轻；`style_key` 只影响推荐理由切入角度。缺省推荐 tone 调整为 `balanced / warm / low / direct`，避免冷启动时过冷或过油。

## v0.3.131 / extension v0.3.85: 多源评估指标与封面图评估（2026-06-20）

后端源码走 `backend-v0.3.131`，浏览器插件走 `extension-v0.3.85`。桌面安装包未改动；如冻结包用户需要同步本次 Web / 后端修复，可后续单独打 `desktop-v0.3.131`。

- **各来源候选补齐互动指标**：`DiscoveredContent`、`discovery_candidates`、`content_cache` 与来源归一化链路新增观看、点赞、收藏、评论、分享、弹幕、转推、书签等字段；B 站 / 小红书 / 抖音 / YouTube / X 能取到的指标会随候选进入统一 evaluator。batch prompt 同时带 `tags/body_text`，并明确互动指标只作辅助，不能用热度覆盖内容与画像的真实匹配。
- **可选多模态 discovery evaluator**：新增 `[discovery].multimodal_evaluation_enabled` 及 batch/图片压缩参数；设置页可开关。开启且当前 evaluation 路由支持图像输入时，候选封面优先从 `data/image-cache/` 读取，未命中才经白名单抓取、缩放和 JPEG 压缩后作为 image input 进入同一 batch evaluator；小红书已缓存头图不再依赖评估时原 CDN token 仍有效。模型不支持或图片准备失败时自动退回纯文本 + 指标评估。
- **多模态 evaluator 明确图片绑定规则**：batch prompt 现在要求模型把 `content_batch[].cover_image_ref = "cover:<content_id>"` 与同一 user message 里对应的图片锚点匹配；有图条目必须结合封面图判断主题、风格、视觉质感和点击诱因，没有 `cover_image_ref` 的条目只按文本字段评估，避免把第 N 张图和候选顺序隐式绑定。
- **浏览器扩展 DOM 采集补齐指标**：小红书被动卡片和抖音 DOM / passive fetch 路径会解析可见的浏览、点赞、收藏、评论、分享数字并回传后端，补齐插件来源候选的评估上下文。
- **抖音 hot discovery 恢复真实召回**：hot board 的 `group_id` 会作为 `seed_aweme_id` 透传到插件任务；扩展后台优先执行带 seed 的热词，并在 DOM 点击 / 被动监听不足时用已登录页面的 related API bridge 拉取 `dy_hot` 候选。MAIN-world fetch tap 同时兼容抖音新搜索页的 `/general/search/stream/` chunked JSON 响应；真实环境中 search 若仍返回 `search_nil_info.search_nil_item="hit_shark"`，会继续按抖音反爬空结果处理。
- **抖音 search 任务补齐真实导航校验**：content script 在首页搜索框输入关键词并点击搜索后，会等待 URL 进入 `/jingxuan/search/<keyword>` 等真实搜索结果路由；任务 debug 新增 `search_navigation_ok` / `search_submit_method`，避免把“只弹出搜索建议或登录弹窗”误报成搜索页已打开。
- **`style_key` 收敛为观看模式词表**：发现 / 推荐链路的 `style_key` 从题材式风格名收敛为 13 个封闭观看状态（如 `deep_focus`、`quick_scan`、`ambient_companion`、`curiosity_spark`）；LLM evaluator prompt、搜索 / 关键词 prompt hints、推荐表达 prompt、规则兜底、推荐兜底文案和轻入口补位同步更新。历史安装的本地数据库会在启动时把已知旧 `style_key` 物理迁移到新 key，运行时也会兼容旧缓存 key。
- **README / 首页同步 release 结构**：用户下载说明明确 `openbiliclaw-v*` 是聚合 Latest Release，`backend-v*` / `extension-v*` / `desktop-v*` 是自动化频道；桌面安装包可能落后于后端源码版本，以聚合页 `Current Channels` 和附带 `.dmg` / `.exe` 为准。
- **插件设置补齐封面图评估开关**：浏览器插件 side panel 的调度 tab 现在也能开关 `[discovery].multimodal_evaluation_enabled`，并编辑图文 batch、封面最大边、JPEG 质量和图片准备超时；保存时保留既有 discovery 配置，避免插件与桌面 Web 设置面脱节。
- **移动 Web 添加到主屏幕补强**：`/m/` manifest 增加 `id` / `scope` / maskable 图标声明，HTML head 增加 `mobile-web-app-capable`、iOS Web Clip 标题与 touch icon；新增后端静态资源契约测试，并修复 degraded 模式下 `/favicon.ico` 被 503 拦截的问题，确保手机保存桌面图标时使用稳定名称、图标和启动路径（不引入 service worker / 离线缓存）。README / README_EN 和官网首页同步补充 iOS「添加到主屏幕」与 Android「安装应用 / 添加到主屏幕」使用说明。
- **补充机会系统统一规格草案**：新增 `docs/plans/2026-06-18-opportunity-system-spec.md`，沉淀画像准确性、OpenCloud / HMA / WorkValue 客户端边界与后续机会系统路线。

## v0.3.130: DeepSeek reasoning_effort 配置保存修复（2026-06-20）

后端源码改动，浏览器插件与桌面安装包未改动。

- **插件 / PC Web 设置页关闭 DeepSeek thinking 立即生效**：`PUT /api/config` 现在允许 `llm.deepseek.reasoning_effort=""` 覆盖已有 `"max"` / `"high"`，并且 `save_config()` 会显式写出 `reasoning_effort = ""`，避免重启后因缺省值回落到 `"max"`。新增 API 与配置 round-trip 回归测试覆盖该路径。

## v0.3.129 / extension v0.3.84: 跨平台行为捕捉统一（2026-06-19）

后端源码走 `backend-v0.3.129`，浏览器插件走 `extension-v0.3.84`。桌面安装包未改动；如冻结包用户需要同步本次捕捉链路修复，可后续单独打 `desktop-v0.3.129`。

- **事件入口批处理不再被单条坏事件打崩**：`POST /api/events` 继续返回 `accepted`，并新增 `rejected` 明细；raw `dislike` 会统一规范为 `feedback` + `feedback_type=dislike`，未知事件只拒绝该条，不再让整批 500 后被插件重试造成重复写入。
- **浏览器插件跨平台行为采集补齐统一 adapter**：B 站、小红书、抖音、YouTube 和 X 都走同一 `PlatformAdapter` / generic collector 事件形态；抖音和 YouTube 除原有 bootstrap / task executor 外，也开始上报普通页面行为事件。
- **统一动作语义和 flush 策略**：B 站补 `follow/share`，小红书补 `share`，抖音 / YouTube 覆盖 `like/favorite/comment/share/follow/dislike`；所有平台 `dislike` 只发送 `feedback`。`follow/share/view` 和带视频停留 metadata 的 `click` 现在会即时 flush，高频 `scroll/hover/snapshot` 仍缓冲去重。
- **真实站点嵌套按钮命中修复**：generic collector 的 click action 识别不再只看原始 `event.target`，会从内部 `span/svg` 向上解析动作元素，并优先选择最近的 `button/[role=button]`，再回退到 `a/[aria-label]/[title]`，避免 X 这类“整张推文卡片也是链接”的 DOM 把 Share 误判成 Reply；X 的 DOM fallback 同时补齐 `aria-label="Share"` 到 `share` 事件的映射。真实 B 站、YouTube、X 视频 / 推文页点击分享按钮已验证会同时写入普通 `click` 和强信号动作事件。
- **新增本机扩展驱动 E2E 捕捉自检**：后端新增 local-only `POST /api/extension/e2e/run` 与 `POST /api/extension/e2e/result`，通过 `/api/runtime-stream` 投递 `extension_e2e_run` 给已安装插件；service worker 打开或复用抖音 / 小红书 / X 标签页，content executor 只执行白名单 DOM 操作（snapshot / scroll / click / share 等），不直接伪造 `BEHAVIOR_EVENT`。后端按运行窗口校验真实 `/api/events` 入库结果；会改变平台状态的 like / favorite / follow / comment / repost 需显式 `allow_state_changing=true`，普通 share 不再被 X 转推 mutation 误匹配。
- **真实三平台捕捉 E2E 续修并通过**：content collector 的 click 监听切到 capture 阶段，避免 X / React 控件在冒泡阶段 `stopPropagation` 后漏掉 Share；scroll 同时覆盖页面和内部滚动容器，解决抖音 / 小红书 feed 容器滚动不进事件的问题。E2E runner 复用同域 tab 时会先归位到平台稳定入口，避免小红书 404 / 风控页或 X 图片预览页污染测试；执行结束后先等待并 flush buffer，再回传 result。真实已登录 Chrome 插件环境下，抖音 / 小红书 / X 的 `snapshot/scroll/click/share` 共 12 个动作全部 extension 执行成功且后端 `/api/events` 匹配成功。

## v0.3.128 / extension v0.3.83: 抖音 DOM-first discovery（2026-06-18）

后端源码走 `backend-v0.3.128`，浏览器插件走 `extension-v0.3.83`。桌面安装包未改动；如冻结包用户需要同步本次 Web / 后端修复，可后续单独打 `desktop-v0.3.128`。

- **抖音 search / hot / feed discovery 改为 DOM-first**：三类插件任务后台 tab 统一先打开抖音首页，再模拟真实 DOM 操作触发搜索、热点或推荐流加载；content script 不再主动跳 `/search/...`、`/hot/...` 快捷 URL，也不再主动调用 search / related / feed API bridge，只被动收集页面自己发出的响应和已渲染 DOM。插件任务为空 / 超时 / 失败时默认返回空结果，direct-cookie fallback 仅保留给显式 `allow_direct_fallback=True` 的诊断路径。
- **抖音 discovery 真实浏览器联调修复**：feed 真实页面当前通过 XHR 发 `/aweme/v2/web/module/feed/`，MAIN-world passive tap 已覆盖 fetch / XHR 两种路径；search / hot / feed 回传前按目标 scope 过滤，避免首页 feed 响应被误计入 search / hot。真实干净会话里 feed 可从首页推荐流回传 `dy_feed` 候选；search / hot 在未登录或入口不可见时保持 DOM-first 但返回空结果。
- **沉淀 agentic 开发过程文档**：新增 `docs/superpowers/` 下的本次设计说明与实施计划，记录抖音 DOM-first discovery 的目标行为、组件边界、测试路径和真实联调约束。

## v0.3.127 / extension v0.3.82: LLM 探针与 Soul 更新链路文档（2026-06-17）

后端源码走 `backend-v0.3.127`，浏览器插件走 `extension-v0.3.82`。桌面安装包未改动；如冻结包用户需要同步本次 Web / 后端修复，可后续单独打 `desktop-v0.3.127`。

- **GitHub Releases 增加聚合 Latest 入口**：新增 `openbiliclaw-v*` 用户发布页，由 `backend-v*` / `extension-v*` / `desktop-v*` 三条 workflow 共同同步；页面会同时展示后端源码 tag、最新插件 zip 与桌面安装包，避免 Releases 首页被单一通道 release 占住。
- **X / Twitter 推荐卡三端归一**：插件 side panel、移动 Web 与桌面 Web 会把 `x` / `twitter` / `x.com` / `twitter.com` 统一归一为 `source_platform="twitter"`，标签显示为 `X (Twitter)`；候选池 source family、点击上报 URL 推断和 fallback URL 也同步映射 X，不再退成 Web 或 B 站。
- **X 文字卡真实 append 链路修复**：`/web`、`/m/` 与插件对 X tweet / thread 或无有效封面的候选渲染文本卡正文；真实后端 + 真实浏览器 E2E 复现到 `/api/recommendations/append` 会把 X tweet 从 pool row 还原成默认 `video` 且丢 `body_text`，现已在 `RecommendationEngine._rows_to_discovered()` 同步映射 `content_type/body_text`。
- **PC Web 平台过滤 tab 从配置驱动**：桌面 Web `/web` 推荐页的 `全部 / B站 / YouTube / ...` tab 现在先读取 `config.sources` 与 `scheduler.pool_source_shares` 中启用的平台，再合并当前推荐列表里实际出现的平台；点击某个平台只过滤当前已加载推荐，没有命中时允许展示空列表。
- **推荐评论反馈改为中性直接反馈**：`feedback_type=comment` 不再默认当正向偏好；事件满意度分类改为 `neutral/direct_feedback`，PreferenceAnalyzer prompt 明确要求根据 `feedback_note` / 备注 / `context` 判断喜欢、不喜欢或仅补充说明。
- **聊天候选进入偏好层的门槛从 AND 改为 OR**：`learn_from_dialogue()` 仍先落 `dialogue` 事件并累计 `insight_candidates.json`，但现在候选满足 `confidence >= 0.8` 或 `occurrences >= 2` 任一条件即可转成 `dialogue_insight` 进入 `PreferenceAnalyzer`。
- **LLM 测试连接输出预算调大**：`LLMProvider.health_check()` 与配置页 `/api/config/probe-service` 的 LLM 探针统一传 `max_tokens=1024`，减少 reasoning-first / OpenAI-compatible provider 在测试连接时被截断成空响应的误报。
- **Soul 架构图与更新流程图重绘**：`docs/diagrams/soul-architecture.html` 和 `docs/diagrams/soul-update-flow.html` 对齐当前真实写回路径、pipeline 输入矩阵和场景示例；`docs/index.md` 同步刷新图表入口。新增 `docs/technical-debt.md`，把画像写入并发风险、Soul 重建 prompt 增长风险迁出 v0.1 todolist。
- **Soul HTML 架构图补齐后台触发器**：`docs/diagrams/soul-architecture.html` 与 `docs/diagrams/soul-update-flow.html` 补充账户同步、runtime soul pipeline tick、speculator / cognition / consolidation 定时节流、探针响应、手动覆盖层和 `discovery_cron` 非消费边界。
- **新增跨平台行为事件技术债记录**：`docs/technical-debt.md` 新增 TD-003，记录当前只有 B 站具备账号侧行为拉取入口，外站 bootstrap / discovery / 插件实时事件尚未统一形成 Soul 维护闭环。
- **补齐 Soul 内部技术债清单**：`docs/technical-debt.md` 新增 TD-004 至 TD-008，记录 ProfileUpdatePipeline 未成为真实单入口、B 站 account sync 已有画像后只更新 preference、聊天学习后台任务未接入 registry、聊天 insight 候选合并依赖精确字符串，以及旧 awareness / insight 公开入口仍保留固定窗口语义。
- **推荐 dislike 批处理补齐候选池清理**：`process_feedback_batch_if_needed()` 现在会 diff 本批新增的 `disliked_topics`，并复用 `purge_pool_for_new_dislikes()` 以后台任务清理 fresh 候选池；普通推荐卡片多次 `dislike` 学到长期避雷项后，不再只更新画像而漏清已有同类候选。
- **热重载补货重启测试稳定性**：`BackgroundTaskRegistry.stats()` 只统计尚未完成的任务，CI 测试改为捕获 `track()` 调度的 task 并等待其完成，不再依赖任务是否仍处于 live 状态。

## extension v0.3.80: 对话历史自动滚到底部（2026-06-16）

浏览器插件小版本发布；后端源码和桌面安装包未改动。

- **对话 tab 历史恢复自动定位最新消息**：popup 启动时即使 Chat view 处于 hidden 状态先 hydrate 历史，用户切到「对话」后也会在下一帧滚到最新消息；追加消息、pending 占位替换、历史恢复共用 `scrollChatMessagesToBottom()`。已用真实临时后端 + unpacked extension 浏览器 E2E 验证 40 turns / 80 bubbles，`bottomDelta=0.5px`、最后一条完全可见。

## v0.3.125 / extension v0.3.79: 画像分类词表 + B 站扩展搜索兜底发版（2026-06-16）

把 `backend-v0.3.124` 之后已合入 main 的跨模块改动打成正式发布：后端源码走 `backend-v0.3.125`，桌面安装包走 `desktop-v0.3.125`，浏览器插件版本提升到 `0.3.79` 并发布 `extension-v0.3.79`。

- **画像一级分类固定词表与迁移**：新增 `soul/taxonomy.py` 的 19 项 `CATEGORY_VOCAB`，`PreferenceAnalyzer` 写入前统一按精确命中 / embedding 最近邻 /「其他」解析；新增 `CategoryMigrator` 与 `profile-consolidate --migrate-categories`，可 dry-run / apply / revert 存量自由分类迁移，LLM 映射必须完整覆盖且目标在词表内。
- **同名异义安全画像整理**：`ProfileConsolidator` 的规则合并改为同名同类限定，同名异类构造强制嫌疑簇送 LLM；judge payload 带 `category`，支持 `{name, category}` 精确引用，no-merge 记忆也按 `name::category` 限定。整理默认覆盖 likes top-512、裁决每批 32 簇，`--full` 可扩到全量标签库。
- **B 站扩展搜索兜底闭环**：当服务端 B 站 search 进入冷却且扩展在线时，后端可入队 bili search task；扩展后台打开真实 B 站搜索页，抓已渲染 DOM 结果回传为 `bili-extension-search` raw candidates，继续走统一 evaluator / admission，并提供真实浏览器 E2E harness。
- **冷启动补货与观测修复**：配置热重载后会重新踢起 classify→文案→delight drain；classify 完成即排文案，不再等下一个 refresh tick；MMR embedding 预热日志区分空池冷启动和真实 embedding 后端故障。
- **发布与文档同步**：README / README_EN、模块文档、架构图入口与 `docs/diagrams/soul-update-flow.html` 对齐当前 main；版本提升到后端 `0.3.125`、插件 `0.3.79`。

## v0.3.124: 统一关键词规划器默认开启（2026-06-15）

把 v0.3.123 引入、一直 flag-gated 默认关的统一关键词规划器 / 背压子系统切到**默认开启**。经确定性端到端 + 真实模型（deepseek 驱动完整 planner）验收后，五个平台的搜索词生成默认走「一次合并 LLM 调用、画像发一份、按平台分块、缺口拉动、逐平台自适应避让 / 水位 / 供给」；旧逐平台生成路径作为可回退兜底逐字保留。后端源码改动，浏览器插件与桌面安装包未改动。

- **`unified_keyword_planner_enabled` 默认 `false` → `true`**：`DiscoveryConfig` 代码默认、`config.example.toml`、`docs/modules/config.md` 一并翻面，无需任何配置即走统一规划器。要回退，把 `[discovery].unified_keyword_planner_enabled` 设为 `false` 并重启后端即可——旧逐平台生成路径逐字保留、回退无副作用（producer / planner 的 flag-off 测试持续覆盖该路径）。⚠️ 装机时从旧 `config.example.toml` 拷过**显式 `false`** 的用户需删掉该行或改 `true` 才会跟随新默认（显式值覆盖默认）。`test_config.py` 默认基线断言同步翻 `True`。
- **合并调用 token 预算修复（默认开启前从 v0.3.123 验收期带出）**：真实模型（deepseek）跑完整 planner 时发现合并生成是全系统输出最大的一次调用（每个 due 平台 × 至多 `gen_batch` 个词同在一个 JSON），固定 `max_tokens` 会把排在 JSON 靠后的平台**截断**、退回兴趣名兜底（实测 5 平台 ×30 词限额偏小时 youtube/twitter 退化成裸兴趣名）。两处修：① block 里给模型的每平台 `need` **收口到 `gen_batch`**——此前给 P3.2 动态水位（可达 `kw_cache_high×3`），而解析每平台只保留 `gen_batch`，「要 80 留 30」既浪费又顶向截断；现在「要多少＝留多少」。② 合并调用 `max_tokens` 改为**按本轮实际要词量动态算** `max(4096, sum(收口后 need) × 48 + 1024)`，随平台数 / `gen_batch` 自适应。真实 deepseek 复跑：五平台各满额 30 词、youtube/twitter 正确出英文、无截断；新增 `test_merged_ask_capped_at_gen_batch` / `test_merged_max_tokens_scales_with_total_ask`。全量非集成测试 2744 passed。
- **觉察/洞察认知链补齐生命周期管理（修两条 soul 技术债）**：① **洞察反馈软作废接线**——`SoulEngine.update_from_feedback` 此前实现了「确认→`validated=True`+置信度≥0.75 / 推翻→`validated=False`+≤0.35」却无任何生产调用方（只有单测），洞察因此只增不减、缺有效失效。新增 `POST /api/insights/feedback`（`InsightFeedbackIn/Out` 模型）把插件洞察卡片的确认/推翻路由进来，`update_from_feedback` 改为返回 `{matched, validated, confidence}` 供端点回传。② **觉察/洞察从固定窗口改游标增量取数**——觉察曾每 tick 固定 `query_events(limit=50)`（>50 的突发静默丢、<50 的安静期重复重发），洞察曾每次全量读觉察（prompt 随 `awareness.json` 无界膨胀）。现觉察按 `last_awareness_event_id` 水位只读新事件、单批容量 300（按 256k+ 长上下文模型设计、正常窗口单次调用即可、不为几十个事件强行分批；超 300 才分批作安全网）、逐批推进水位（中途失败不丢已处理批）、首批附 10 条已处理事件作趋势上下文、积压超 900 跳窗并 WARNING；洞察按 `last_insight_awareness_index` 位置游标只读新觉察、单批 150、把当前活跃假设作 `existing_hypotheses` 上下文透传（`build_insight_prompt` 新增形参，system 仍静态、prompt-cache 不破）；批量 LLM 调用 `max_tokens` 调大到 32768，两 analyzer 的 `analyze()` 新增 `max_tokens` 形参。`query_events` 新增 `after_event_id` 过滤（db + manager）。新增 `tests/test_api_insight_feedback.py`（端到端校准）+ `test_cognition_cycle.py` 五个游标/分批用例（覆盖不漏、不重复处理、空跳过、中途失败保留进度、洞察游标 + 上下文）。全量非集成测试 2754 passed。
- **洞察「准 / 不准」按钮接入三端 UI**：把上一条新增的 `POST /api/insights/feedback` 端点接到全部三个前端面——浏览器插件 popup（`popup-api.js` 新增 `submitInsightFeedback` + `renderActiveInsights` 加按钮 + 乐观更新置信度/已确认态 + popup.html 配套 CSS）、响应式/手机 web（`web/js/api.js` + `views/profile.js` 镜像现有 speculative 的 confirm/reject 模式，回写 state 后重渲染）、桌面 web（`web/desktop/assets/js/app.js` insightsHtml 加按钮 + `respondInsightFeedback` + app.css 配套样式）。点击后路由进 `update_from_feedback` 校准该假设并刷新画像。**真实浏览器端到端验证时发现并修复一个真问题**：`update_from_feedback` 此前只改 `insight` 层，而 UI 的 `/api/profile-summary` 与 delight 打分读的是 `soul` 层缓存的 `active_insights` 窗口快照——校准因此不会立即对用户可见 / 不影响推荐，要等下一次 12h 认知 sync 才生效。修复：命中后新增 `_sync_insight_to_soul_snapshot` 同步把置信度/`validated` 写进 soul 层快照并重渲染画像文件；`test_api_insight_feedback.py` 加 soul-snapshot 断言守护回归。扩展新增 `submitInsightFeedback` 单测，扩展全量 462 测试通过；三端 JS 语法 + 扩展 tsc 类型检查均通过；用真实 DeepSeek 生成洞察后浏览器实测闭环（桌面 web `/web` reject 65%→35%、手机 web `/m` confirm 35%→75%+已验证，API/磁盘/反馈事件均一致）。⚠️ 触达浏览器插件，发版需打 `extension-v*` tag。
- **B 站搜索风控冷却：全局急停 → 分级软冷却（治理「补货 novelty 被一次风暴团灭」）**：针对用户反馈的候选池补货慢，定位到主因之一——`search` 与 `explore` 共用同一把进程级搜索冷却，而**单个被 `v_voucher` 风控的关键词**就会触发 600s 全局急停、把两个新鲜内容来源同时打死十几分钟（冷却还会升级到 1800s），期间只剩 trending/related_chain 反复捞已知项、每轮净新候选跌到个位数。本次把冷却分级：① **412 与 `v_voucher` 拆开**——412 是显式 IP 封禁，保留即时硬冷却（base 600s，`_SEARCH_COOLDOWN_412_SECONDS`）；`v_voucher` 多为 WBI key churn / 轻限流，改走阈值化软冷却。② **阈值化**：单关键词耗尽重试只 `_record_voucher_block()` 记一次 streak、**不**触发冷却（整轮其余关键词 + 共用此冷却的 explore 继续出货），连续 `_SEARCH_VOUCHER_BLOCK_THRESHOLD`（默认 3）个关键词级耗尽才启用进程级 cooldown，base 从 600s 缩到 **180s**。③ **快探测**：一旦 `streak>0`（怀疑风暴），后续关键词只做单次探测、不再每词 ~21s 硬抗（避免真限流时越捅越深），任一成功即 `_reset_search_cooldown_backoff()` 清零 streak 与升级档位。`_activate_search_cooldown()` 增 `base_seconds` 形参区分两类 base。后端源码改动，浏览器插件与桌面安装包未改动。新增 `tests/test_bilibili_api.py` 四个单元用例（单关键词不触发、连续达阈值触发、成功清零 streak、412 即时硬冷却），既有「一个关键词＝风暴」的旧断言同步改写；另加 `tests/test_search_strategy.py` 三个**端到端**用例——用真实 `BilibiliAPIClient`（真冷却逻辑 + 策略自身 storm-abort）只 fake HTTP 边界，验证「单关键词风控不打断整轮 search」「连续风暴仍退避且 q4 不再发请求」「explore 共用此冷却时被同步门控」。全量非集成测试 2760 passed。
- **B 站扩展搜索兜底后端 Phase 1（Lever 1.5）**：新增 `sources/bili_tasks.py`、`runtime/bilibili_producer.py` 和 `/api/sources/bili/{next-task,task-result,kick}` 三个端点，采用“API 搜索为主、扩展只在 search 冷却时兜底”的策略：只有 `search_cooldown_remaining()>0`、扩展 presence 在线、B 站平台族低于 quota 且候选待评估池未满时才入队搜索任务。扩展回传的视频结果会转成 `source_strategy="bili-extension-search"` 的 raw candidates 写入 `discovery_candidates`，继续走统一 evaluator / admission；统一关键词 planner 开启时会 claim B 站关键词并通过 `source_keyword_id` 回填 yield 生命周期。当前提交只完成后端闭环与 mockable 测试，扩展 DOM 搜索执行器留到 Phase 2。
- **B 站扩展搜索兜底 Phase 2（真实浏览器 DOM 执行器）**：浏览器插件新增 `background/bili-task-dispatcher.ts` 和 `content/bili/task-executor.ts`，service worker 开始响应 `bili_task_available` 并轮询 `/api/sources/bili/next-task`。领取 search task 后扩展用后台 tab 打开 `search.bilibili.com/all?keyword=...`，只抓真实页面已渲染的搜索结果卡片（BV、标题、UP、封面、播放数、时长、简介），通过 `BILI_TASK_RESULT` 回传 `/api/sources/bili/task-result`；仍不直连 B 站 API、不伪造 WBI 签名、不直接写推荐池。新增 `extension/tests/bili-task-dispatcher.test.ts` 与 `extension/tests/bili-task-executor.test.ts`，并用真实 B 站搜索页验证当前 selector 可抓到 42 个结果卡。
- **B 站扩展搜索兜底 Phase 3（producer → presence → 真实扩展自动触发 E2E）**：新增默认跳过的真实浏览器 harness：`BILI_EXTENSION_E2E=1 .venv/bin/pytest tests/test_bili_extension_browser_e2e.py -q -s` 会启动临时 FastAPI app + 临时 SQLite，用 Playwright 持久上下文加载 unpacked extension，等待真实 runtime-stream presence，再把进程内 `BilibiliAPIClient` 置入 search cooldown，调用真实 `BilibiliExtensionSearchProducer` 入队并通过 `bili_task_available` 唤醒扩展。测试要求扩展领取 `/api/sources/bili/next-task`、打开真实 `search.bilibili.com` 搜索页、抓 DOM 卡片并 POST `/api/sources/bili/task-result`；实测关键词 `机械键盘 声音` 完成 1 个 task，回传 3 条真实 BV。该 harness 不污染生产数据库、不新增生产 debug endpoint；同时新增 helper 单测覆盖 Chrome/Playwright 解析、free port、CDP target 选择和 cleanup 范围。
- **热重载不再清空冷启动补货流水线（lever 2a）**：`PUT /api/config` 触发的热重载会先 `cancel_all` 取消在途后台任务，其中包括 classify_pool_backlog / 文案预计算 / delight 评分——冷启动期边调设置边等出货的用户因此每次保存都把补货进度清零、最坏要等到下一个 60s 刷新 tick 才恢复。`restart_background_tasks()` 现在在重建组件后，除了原有的 speculator / prewarm 重启，额外经 `_safe_post_reload_precompute()` 在**新引擎**上补调一次 `precompute_pool_copy(profile=...)`（内部 detached 再启 classify 与 delight），让 classify→文案→delight drain 立即恢复而非干等；其自带 `_expression_lock` 保证不与刷新轮询周期 drain 抢同批，刷新 loop 仍是兜底。后端源码改动，浏览器插件与桌面安装包未改动。新增 `tests/test_api_app.py` 两个用例：`test_restart_tasks_rekicks_pool_precompute_drain`（断言重启后 `post_reload_precompute_pool_copy` 被调度且以当前 profile 调用）+ `test_e2e_hot_reload_resumes_real_pool_fill`（**端到端**：用真实 `RecommendationEngine` + 真实 `Database`、只 fake LLM 文案——seed 一条「已分类、缺文案」候选 `count_pool_candidates()==0`，走真实 `restart_background_tasks()` 触发后,候选被真实 `precompute_pool_copy` 写入文案、变为 `count_pool_candidates()==1` 可服务）；既有 `recommendation_engine=object()` 的重启用例因 `getattr` 缺该方法而天然不受影响。全量非集成测试 2763 passed。
- **classify 完成即排文案、不等下一个 tick（lever 2b）**：`precompute_pool_copy` 早先把 `classify_pool_backlog` detached 后立刻读「待文案」候选——但刚被 detached classify 分类好的条目要等下一个 60s 刷新 tick 才会被排文案，白白多一个「已分类但缺 `pool_expression`、被可用性闸门挡住」的窗口。本次把文案生成抽成 copy-only 的 `_drain_expression_copy()`（不再 spawn classify、避免递归），并在 `_safe_classify_pool_backlog` 里 classify 出新条目后**当场 await 一次文案排版**——分类→文案在同一周期内串起来；共享 `_expression_lock` 保证与常规 precompute 不抢同批、不重复花 token。`precompute_pool_copy` 改为复用 `_drain_expression_copy`，对外行为不变。后端源码改动，浏览器插件与桌面安装包未改动。新增 `tests/test_recommendation_engine.py` 两个用例：`test_safe_classify_pool_backlog_drains_copy_for_newly_classified`（seed 未分类候选 `count_pool_candidates()==0`，调 `_safe_classify_pool_backlog` 后经真实 classify + copy 变为 `==1`，断言 `recommendation.evaluate_batch` 与 `recommendation.write_expression` 两个 caller 都被调用）+ `test_e2e_precompute_pool_copy_classifies_then_copies_in_one_pass`（**端到端**：走生产入口 `precompute_pool_copy`、真实 engine + DB、只 fake LLM——其自身 copy drain 此时还是 `==0`，await detached classify 链跑完后 2b 补文案、变 `==1`）。全量非集成测试 2766 passed。
- **prewarm 日志区分「空池冷启动」与「嵌入后端故障」（lever 4 观测）**：`prewarm_pool_mmr_embeddings` 早先无论是「没配 embedding / 池子还空」还是「Ollama 真挂了」都一律返回 `0`，启动重试包装器照样打 5 行吓人的 `warmed=0 — retry` + `gave up`，运维**分不清良性冷启动和真故障**（最初诊断 XG 那条日志就踩了这个坑）。现在 prewarm 返回分三档:`>0` 已暖 / `0` 有候选但全嵌入失败＝后端不可达（值得重试）/ `-1` 没东西可暖（无 embedding service 或空池＝良性、重试无意义）;启动包装器据此:`-1` 直接平静跳过(不再刷 5 行告警)、`0` 才重试到底并在放弃时打 **WARNING** 点名「embedding 后端不可达、MMR 多样性降级」;`warm_mmr_embeddings` 的逐条 embed 失败仍在 DEBUG 留痕。后端源码改动,浏览器插件与桌面安装包未改动。新增 `tests/test_recommendation_engine.py::test_prewarm_pool_mmr_embeddings_signals_distinguish_states`(四档返回:无 embedding / 空池 / 后端挂 / 正常)+ `tests/test_api_app.py::test_startup_prewarm_wrapper_skips_retries_on_nothing_to_warm`(`-1` 只调一次、`0` 重试 5 次)。全量非集成测试 2768 passed。

## v0.3.123: 统一各来源 profile prompt 输入（移除人格素描总结）（2026-06-14）

把此前散落在发现 / 推荐 / 探测器各处、字段各异的画像 prompt 输入收敛成**同一份**结构化画像，并从所有 LLM 输入里移除 `personality_portrait` 那段总结性叙事——人格素描仍照常生成并在画像页展示，只是不再喂任何 prompt。后端源码改动，浏览器插件与桌面安装包未改动。

- **发现与推荐共用同一份画像输入**：`build_profile_summary()`（discovery）成为唯一的结构化画像序列化器，`_recommendation_profile_summary()` 改为直接委托它——推荐喂给 LLM 的画像因此与发现完全一致，并补齐了之前缺的 `values` / `cognitive_style` / `motivational_drivers` / `current_phase` / `life_stage` / `source_platform_mix` / `recent_awareness` / `mbti` / `interest_domains` 等字段。`include_active_insights` 形参移除（统一输入恒含 active_insights）；embedding 选出的内容相关兴趣经新增 `interests=` 形参透传。
- **移除人格素描总结进 prompt**：`build_profile_summary()` 不再输出 `personality_portrait`；`OnionProfile.to_llm_context()` / `SoulProfile.to_llm_context()` 新增 `include_portrait` 开关，兴趣探测（speculator）与规避探测（avoidance_speculator）传 `include_portrait=False`。理由：结构化字段已承载同样信号，而 prose 里的比喻 / 例子还会带偏 query 与文案生成。人格素描照常生成、在画像页 / 桌面端展示、参与 overrides，仅不进任何 LLM prompt；eval / persona 渲染保留默认（画像总结是 persona 真值）。
- **配套 prompt 指令清理**：explore domains prompt 第 12 条改为「只依赖 `interests` / `interest_domains` 判断兴趣方向、不要从人格描述反推」（不再点名 `personality_portrait`）；speculation 生成 prompt 的信号权重从「portrait + deep_needs + motivational_drivers」改为「deep_needs + motivational_drivers」。系统 prompt 仍保持 100% 静态，prompt-cache 约定不破。
- **画像字段上限统一抬到 30**：`build_profile_summary` 里 `cognitive_style` / `values` / `motivational_drivers` / `deep_needs` 原 `[:5]` → `[:30]`（与 `core_traits` 对齐）；`recent_awareness` / `active_insights` 窗口取最新 `[-5:]` → `[-30:]`；`mbti.inferred_from`、`active_insights[].evidence`、`speculative_interests`、每域 specifics（`_SPECIFICS_PER_DOMAIN`）一并 `5` → `30`。注：`_SPECIFICS_PER_DOMAIN` 抬高对重度画像 token 影响最大（128 域 × 每域至多 30），扁平 `interests`（256）已全局承载最强 specifics。
- **X / 小红书 / 抖音关键词生成并入统一画像**：此前 X / 小红书的搜索关键词生成只喂 top-15 兴趣的 `name｜category｜weight` 元组（各自精简 prompt），现改为吃完整 `build_profile_summary`（与 B站 / YouTube 关键词生成一致），取消 top-15 截断、带上 `disliked_topics` 避雷。抖音原本是确定性逻辑（直接取兴趣名、不调 LLM，即设计里一直 deferred 的 `dy_explore`），现也补上 LLM 关键词生成：同样吃 `build_profile_summary`、带 Douyin-风格静态 system prompt，并在**无 `llm_service` / 调用失败 / 返回为空**时回退到确定性兴趣名（`seed_keywords` 仍最高优先）。至此**生成阶段用画像调 LLM 的子任务**：B站 search/trending/explore、YouTube yt_search、X x-search、小红书 xhs-search、抖音 search。五平台的**内容评估**环节本就共用 `build_profile_summary`。各平台仍保留各自平台风格的静态 system prompt（prompt-cache 不破）。全量非集成测试通过。
- **统一关键词 planner / 背压子系统落地（P1，flag-gated，默认关）**：在 `[discovery].unified_keyword_planner_enabled`（默认 `false`）后面新增一套「双缓冲 + 缺口拉动」背压，把五个 search 关键词生成器（B站 `search` / 小红书 `xhs-search` / 抖音 `search` / YouTube `yt_search` / X `x-search`）从「各自逐平台调 LLM、各发一份画像」收敛为**一次合并调用、画像只发一份、按平台分块**（`trending/explore/related/hot/feed` 等非 search 路径原样不动）。链路：`discovery_keywords` 存储（`pending→claimed→used/failed/executing` 状态机 + 在途三元组部分唯一 + 租约回收 + CAS 单飞锁，锁在调 LLM 前释放）→ `KeywordPlanner`（缺口拉动合并生成 + `profile_kw_digest` 失效 + LLM 失败回退确定性兴趣名 + 稀疏画像回收最旧 `used`）→ `KeywordFetchCoordinator`（缺口 + 各平台 `min_interval` 闸门下 claim，三执行形态：B站/抖音内联评估即 `used`、X/YouTube fetch-only 交 `DiscoveryCandidatePipeline` 延后 admit、小红书真异步 `executing`→task-result 回调 `used`；预算拒回滚 `claimed→pending`）→ 候选全程透传 `source_keyword_id`、入池按 `(keyword,content)` 幂等回填 `yield_count`、0 产出退役。**成本归因**：合并调用一次 response、token 不可平台间拆分 → 记单一 caller `discovery.keyword_planner`（`cost --by caller` 可见 search 关键词总成本塌缩），per-platform 不冒充 token 拆分而靠 planner 每轮 emit 的结构化 `cycle ledger`（`{platform: {generated, yield}}`，新增 `Database.keyword_yield_total()` 提供累计 yield）观测。**默认关、旧逐平台生成路径逐字保留可回退**；flag-on 端到端正确性由新增 `tests/test_keyword_backpressure_e2e.py` 覆盖（真实 store + planner + coordinator + engine + pipeline，仅 fake LLM/平台 IO）。全量非集成测试 2718 passed。
- **统一关键词 planner P2 打磨（供给优势 / 弃权 / 轮换，仍默认关）**：合并 prompt 静态 system 加**平台供给优势表**（B站 学习/梗、小红书 生活/美妆、抖音 娱乐/热点、YouTube 英文长内容、X 实时/英文），模型据此把兴趣映射到各平台强项；新增**弃权**——供给不匹配的平台可少出 / 返回 `[]`，planner 区分「弃权（成功调用 + 平台返回空 → 不回退、本轮跳过）」与「整次调用失败（→ 所有 due 平台回退确定性兴趣名）」；轮换上 `claim_keywords` 严格 FIFO（最旧 pending 先出）+ 非弃权平台生成后仍低于低水位则按缺口 `recycle_oldest_used` 补足。per-platform 饱和粒度仍留 P3。全量非集成测试 2732 passed。
- **统一关键词 planner P3 自适应（per-platform 饱和避让 + 动态缓存水位 + 数据驱动供给优势，仍默认关）**：把 P2 还留在全局粒度的避让 / 缓存 / 供给三处收到**逐平台 + 数据驱动**。①**饱和避让逐平台化**：新增 `Database.get_pool_topic_counts_by_platform()`（与 servable 同口径，按 `source_platform` 分组），`KeywordPlanner._avoid_hints()` 据此算出**每个平台自己池里**已饱和的 `topic_group`（阈值 `max(5, 本平台池量//5)`、top-12），只写进该平台的合并 prompt 分块；池量不足 floor 10（冷启动）的平台回退到全局热门 topic 避让——「小红书池里美妆已满」只压小红书的美妆词、不再误伤 B站。②**缓存高水位动态化**：新增 `Database.used_keyword_count()`，`_target_high(platform)` 用 `ceil(本平台缺口 / 平均单词产出)` 估算该平台该囤多少词，`平均产出 = keyword_yield_total / used_keyword_count`（需 ≥10 个 `used` 样本才采信），夹在 `[max(1, kw_cache_low + fetch_batch), kw_cache_high*3]`；样本不足 / 无缺口 / 平均产出为 0 时回退静态 `kw_cache_high`——高产平台少囤、低产平台多囤，缓存深度随真实 admit 产出自适应。③**供给优势从静态先验补上数据驱动**：P2.1 的 `<supply_advantage>` 是平台刻板印象的静态表，P3.3 在其上叠一层**该用户真实 admit 历史**——新增 `Database.get_admitted_topic_counts_by_platform()`（口径与 P3.1 不同：统计每平台历来入过缓存、非 dislike、可链接的 `topic_group`，不限是否已服务/已看），`KeywordPlanner._supply_hints()` 取各平台 top-8（阈值 `max(3, 入池量//10)`、入池量 <10 则空）并**减去该平台当前 `avoid_topics`**（「擅长但当前饱和」只留在避让、绝不同时主推），作为每平台 `supply_hint` 写进合并 prompt 分块；静态 system 仅描述该字段语义、`<supply_advantage>` 表与 prompt-cache 不破，冷启动无历史则字段为空、模型只依据静态表。用户在某平台稳定看某偏门主题（如抖音硬核科普）时 planner 会学到并优先映射，而非死守平台刻板印象。仍默认关、flag-off 逐字回退。全量非集成测试 2742 passed。
## v0.3.122: 画像 prompt 截断治理 + 自动更新守卫落地（2026-06-13）

对真实画像（千级兴趣标签、95 条避雷项）做了一次截断审计后的三项修复：整理任务覆盖整个有意义的标签存量、避雷项进 prompt 零截断、近期觉察/洞察改取最新。另外把 v0.3.121 changelog 已宣称但代码未随 tag 落地的自动更新守卫补强真正合入（`backend-v0.3.121` 不含该实现，git 安装需升到本版才生效）。后端源码更新走 `backend-v0.3.122`，桌面安装包走 `desktop-v0.3.122`（冻结包不能自动更新，v0.3.121/122 的改进需换包获得）；浏览器插件未改动（仍为 `0.3.78`）。

- **画像 prompt 兴趣上限再放宽（256 / 128 / 30）**：扁平兴趣 tag 64 → 256（discovery 摘要 + 推荐摘要 + `_select_relevant_interests` embedding 候选池三处对齐）、一级兴趣域 8 → 128、`core_traits` 5 → 30。实测真实画像下 0.6–0.7 权重区间此前有 33 个有效兴趣对 LLM 完全不可见，现全部进入。代价：discovery 摘要 ~18K → ~62K 字符（≈2.5 万 tokens/调用）、推荐摘要 ~7.7K → ~23.5K 字符；各调用点 max_tokens 无需调整（输出体积不随画像输入增长），但单调用输入成本上升，依赖 prompt 前缀缓存摊薄，可用 `openbiliclaw cost --by caller` 观察缓存命中。
- **扁平兴趣填充改为全局权重排序**：`_extract_interest_tags` 的 specifics 填充取消每域 top-5 配额——真实画像里「娱乐」域挂着 204 个 specifics，0.83 权重的「网络热梗与模仿」被域配额挡在外面，而小域 0.38 权重的标签反而进了 prompt。改为域 tag 全放 + 剩余名额按 specific 自身权重全局排序后，实测 ≥0.5 权重的兴趣 100% 进入 LLM 画像输入（改前 ≥0.7 区间尚有 7 个不可见）。域级多样性由域 tag 与 `interest_domains` 区保证。CLI `profile-consolidate` 帮助文案同步 top-512 / 分批裁决。
- **画像整理覆盖范围 top-128 → top-512 + LLM 裁决分批**：实测千级兴趣标签存量下，整理只摸得到权重 top-128，绝大多数措辞变体永远在边界外；`_LIKES_BOUNDARY` 提到 512 后整理覆盖整个有意义的存量（深尾留给权重衰减）。配套把单次 LLM 裁决改为**每批 32 簇分批调用**——宽边界首轮可能产出上百个簇，单次大调用会把 JSON 输出顶到 token 上限截断在半截字符串上、全部簇被拒；分批后单批失败只丢本批（下轮重聚类），其余照常应用。no-merge 记忆上限 4000 → 16000 适配宽边界。
- **避雷项进 prompt 不再截断**：discovery / 推荐两侧的 `disliked_topics` 画像输入上限 64 → 128，与存储上限（`_DISLIKED_TOPICS_STORE_CAP=128`）对齐——近因并集修复（v0.3.121）之前的存量条目仍按字典序排列，64 截断等于"按拼音首字母决定哪些雷点对 LLM 可见"，95 个存量避雷项有 31 个从未进过 prompt。
- **近期觉察 / 洞察截断取最新而非最旧**：`recent_awareness` / `active_insights` 窗口按时间旧→新存储（cognition_cycle 取尾部），但全部 8 处消费端用 `[:5]` 切片——进 discovery / 推荐 / delight prompt、画像 markdown 镜像和 portrait 重生成的一直是**最旧** 5 条（字段名叫 recent，实际喂的是 least recent）。统一改为 `[-5:]` 取最新。
- **自动更新守卫补强**：git 命令执行从线程池 `subprocess.run` 改为 `asyncio.create_subprocess_exec`，避免 Windows 后端长时间运行后命令异常返回；自动应用前改跑 `git fetch --force --tags origin`，解决本地旧 tag 遇到远端重打时的 `would clobber existing tag`；dirty worktree guard 继续阻止已跟踪文件的工作区改动，但不再被 `uv.lock`、未跟踪文件、纯 index-only 条目和本地 `ollama-models/` 阻塞；GitHub tag 查询遇到证书校验类错误时降级重试一次，兜底 Windows 打包环境证书链缺失。

## v0.3.121: 12 小时画像自动整理（2026-06-12）

画像从「只进不出地积累」变成「定期自我整理」：新增 ProfileConsolidator，每 12 小时按「规则合并 → embedding 聚类 → LLM 裁决 → 校验执行」流水线清理兴趣 / 避雷主题的措辞变体，应用前自动备份、可一键回滚；配套把画像有效上限提升到 64、画像输出去掉 UP 主维度并修复偏好合并 bug。后端源码更新走 `backend-v0.3.121`；浏览器插件与桌面安装包未改动（插件仍为 `0.3.78`）。

- **discovery / 评估画像输入上限放宽**：画像摘要扁平兴趣 tag 上限 10 → 30，兴趣域 / 兴趣 tag 一律按 weight 降序排序后再截断（域 tag 优先填充，画像越丰富的用户不再被列表顺序随机砍掉强兴趣）；`disliked_topics` 上限 discovery 侧 8 → 16、推荐侧 5 → 16；负例锚定 `negative_exemplars.MAX_LIMIT` 8 → 16；batch 评估 payload 的 `description` 截断 200 → 400 字符；`_select_relevant_interests()` embedding 候选池改为按 weight 排序取前 15。
- **画像输出去掉 UP 主维度 + 偏好合并 bug 修复 + 避雷项近因排序**（接上一条的后续）：
  - `build_profile_summary()` 不再输出 `favorite_up_users`，`build_search_queries_prompt` 同步删掉配套规则——避免模型从「常看某 UP」反推内容兴趣。用户的 UP 主清单仍在 `/api/profile-summary` 用户视图可见可编辑，并继续给 `RelatedChainStrategy` 当种子，只是不进 LLM 画像输出。
  - 修复 `merge_preferences` 的 `favorite_up_users` 合并 bug：此前「本批一旦提到任意创作者就用本批列表整体替换历史」会丢掉之前确认过的 UP 主，改为旧 ∪ 新真正累积（与注释里声明的语义一致，`RelatedChainStrategy` 种子因此不再被偶发批次冲掉）。
  - `disliked_topics` 合并从字典序集合并集改为**近因有序并集 + 上限 40**：本轮避雷项排在前，下游 `[:16]` 截断保留最新 / 最相关的雷点而非字典序靠前的那批；长期不再出现的雷点滑出尾部衰减。
  - `build_preference_analysis_prompt` 每轮兴趣 tag 上限 5~15 → 5~25（证据充分可多提，不足时仍少提低权重，不凑数），让冷启动 / 富历史用户首轮就能填满放宽后的 30 槽画像输出。
  - 推荐重评估 / 批量文案 / delight 评分 / delight 理由四处候选 `description` 截断统一对齐 400 字符（原 200 / 300 / 280），与 discovery 评估一致；MMR 去重 embedding 文本保持不变（缓存 key）。
- **12 小时画像整理任务（ProfileConsolidator）**：新增 `soul/consolidator.py` + CLI `profile-consolidate`（默认 dry-run / `--apply` / `--revert <run_id>`）+ `[scheduler].profile_consolidation_enabled/interval_hours`（默认开、12h）。流水线：规则层同名合并（实测真实画像零成本干掉 64 组同名标签）→ embedding 聚类 → no-merge 记忆 → 单次 LLM 输出 merge/keep 操作 → 代码严格校验后执行；避雷主题严禁向上泛化；rename 穿透用户覆盖层；应用即备份可回滚，回滚后不复发；应用后向插件推「画像整理」认知卡片。稳态（输入 digest 未变 / 簇已判过）每轮零 LLM 调用。
- **画像有效上限提升到 64**：`interests` / `disliked_topics` 的 LLM 画像输入上限统一 30 / 16 → 64（discovery 摘要 + 推荐摘要 + `_select_relevant_interests` embedding 候选池三处对齐）；`disliked_topics` 存储上限 40 → 128（展示上限的 2 倍，给近因重排和后续 LLM 整理留边界余量）。与 12 小时画像整理任务配套：整理卡 64 边界做同义合并，保证截断进 prompt 的是 64 个彼此不同的概念。
- **CLI 命令的 LLM 调用补记成本台账**：`cli.py` 新增共享 `_build_usage_recorder()`，CLI 自建的 `LLMService` / `SoulEngine` 五处（推荐引擎、发现引擎、`profile-consolidate`、xhs 关键词生产、soul 引擎）与 openclaw bootstrap 两处统一接上 `UsageRecorder`。此前只有 daemon 路径（`runtime_context`）挂了 recorder，CLI 手跑的命令（如 `profile-consolidate` 的 `soul.consolidation` 裁决调用）不进 `llm_usage` 表，`openbiliclaw cost --by caller` 完全看不到。

## v0.3.120 / extension v0.3.78: 桌面安装包更新提醒（2026-06-11）

桌面安装包用户从「完全不知道有新版本」变成「自动收到下载提醒」：冻结包后台改跑 check-only 循环，跟踪 `desktop-v*` 安装包 tag，发现新包时设置页提示并附直达下载链接。同时合入惊喜推荐加载数量三端统一。后端源码更新走 `backend-v0.3.120`，桌面安装包走 `desktop-v0.3.120`；浏览器插件版本提升到 `0.3.78`，发布 `extension-v0.3.78`。

- **冻结包定期检查新安装包并提醒下载**：`check_and_update_if_due` 对 frozen 走 check-only 分支——**无论自动更新开关状态**都按检查间隔轮询（`_background_loop_enabled()` 对 frozen 恒真，开关只管自动应用而 frozen 永远不能应用），发现新包置 `update_available` 并推 `backend_update_available` 事件；`check_and_update_now` 同样在非 git 形态下只报告不应用，避免 apply 尝试把刚发现的 `update_available` 状态覆写成 unsupported。v0.3.119 的 apply 拒绝守卫不变，双重兜底。
- **冻结包更新通道切换到 `desktop-v*` 安装包 tag**：新增 `_parse_desktop_candidate` / `_fetch_latest_candidate(channel=...)`，frozen 形态的 `check_now` 只比对 `desktop-v*` tag（无 legacy 兜底）——`backend-v*` 源码 tag 与安装包不总是同步发布（如 v0.3.118 只发了源码 tag），桌面用户只该在真有新安装包时被提醒。
- **设置页冻结态提醒 UI**：新增 `describeFrozenUpdateStatus` 分支文案（「发现新版安装包 vX.Y.Z…请下载新版安装包完成升级」/「当前安装包已是最新」等），`update_available` 时显示「前往下载新安装包」按钮直达对应 `desktop-v*` Release 页；「立即检查」在冻结态可用，「立即应用」保持隐藏；`backend_update_available` 事件到达时按 tag 前缀区分文案弹 toast 提醒（安装包 → 引导下载，源码 → 普通提示）。开关与间隔输入在冻结态仍禁用（它们只管自动应用）。
- **惊喜推荐加载数量三端统一生效**：新增 `[scheduler].delight_queue_limit`（默认 `20`，范围 `1..100`），`/api/delight/pending-batch` 在未显式传 `limit` 时读取该配置。桌面 Web 设置页保存该字段，插件 side panel 和移动 Web 默认不再写死 `20`，因此同一配置会随下一次队列拉取在三端同步生效。

## v0.3.119: 自动更新冻结包守卫与状态体验（2026-06-11）

接 v0.3.115 的自动更新解卡，堵死桌面安装包的「无限重启循环」高危隐患，并补齐自动更新的状态展示与手动操作缺口。后端源码更新走 `backend-v0.3.119`，桌面安装包走 `desktop-v0.3.119`；浏览器插件未改动，仍为 `0.3.77`。

- **桌面安装包不再会被自动更新拖入无限重启循环**：`AutoUpdateService` 的 apply 路径与后台调度循环（`_check_apply_guards` / `check_and_update_if_due`）新增显式 `install_mode != "git"` 守卫——桌面冻结包即便与 AI / 一键安装共用 `~/OpenBiliClaw` 目录（`entry.py` 把 `OPENBILICLAW_PROJECT_ROOT` 指向它）、继承了 `auto_update_enabled=true`，也不再 fast-forward 那个 git 检出。此前 `detect_install_mode()` 的 `frozen` 仅用于前端显示，服务端 apply 路径漏判：冻结包会真的 `git merge` 改写他人源码 + venv，而捆绑二进制重启后仍跑旧码，每个检查周期重复 = 无限重启循环（且冻结态前端开关被禁用，用户关不掉）。真实 PyInstaller 安装包端到端实测：apply（真实可信 remote + 真实可快进 0.3.118 目标）被 `unsupported_install_mode` 拦截、co-located git 检出零改动、后台循环同样不应用。
- **设置页补上「立即检查 / 立即应用」按钮**：桌面 Web 设置页加上规格要求的两个按钮（`POST /api/update/check`、`/api/update/apply`），并在收到 `backend_update_available` / `backend_restart_pending` / `backend_update_failed` 运行时事件时实时刷新状态行，更新全程不再无感知。
- **配置保存不再丢更新状态**：保存配置触发热重载重建服务时，通过 `AutoUpdateService.adopt_status_from` 携带上次检查结果，状态行不再从「发现新版本」回退到「尚未检查更新」（瞬态 `checking` / `applying` 状态不携带，避免误表上一实例的在途 apply）。
- **降级模式也能检查 / 拉取更新**：降级模式（LLM 注册表不可用）放行 `/api/update-status`、`/api/update/check`、`/api/update/apply`，且降级上下文现在构建真正的 `AutoUpdateService`——LLM 配坏正是需要拉取修复版本之时。真实环境实测：git 模式 0.3.118 → 0.3.119 完整升级（ff-merge + uv sync + execv 重启）、三守卫、保存状态保留、降级 `update-status` 返回 200 全部通过。

## v0.3.118 / extension v0.3.77: 初始化来源可选化（2026-06-11）

B 站不再是初始化的强制基座：CLI、插件面板、桌面 Web 和安装包 `/setup/` 都可以在初始化前取消勾选 B 站，只要至少保留一个数据来源即可。同时修复插件连接徽章与保存列表的响应性问题。

- 后端包版本提升到 `v0.3.118`，浏览器插件版本提升到 `0.3.77`，准备发布 `backend-v0.3.118` 与 `extension-v0.3.77`。
- 初始化不再强制 B 站：B 站在所有初始化入口（CLI / 插件面板 / 桌面 Web / `/setup/` 向导）变为与小红书 / 抖音 / YouTube / X 同级的可选来源——默认勾选（推荐）但可取消，**至少保留一个来源**。CLI 新增 `--no-bilibili` / `OPENBILICLAW_NO_BILIBILI=1`（同时持久化 `[sources.bilibili].enabled=false`），全来源关闭直接报错退出；共享流水线 `run_guided_init` 新增 `include_bili`（False 时跳过 B 站拉取，`client` 可为 None），所有所选来源 0 信号时以新失败码 `empty_signals` 终止；X 点赞 / 收藏补进画像构建输入（保证 X-only 初始化也有画像素材）。API 侧：`GET /api/init-status` 的 `can_start` 不再硬性要求 B 站登录（`bilibili_logged_in` 仍下发，三端前端在勾选 B 站时自行拦截并提示「登录或取消勾选」），`POST /api/init` 仅当所选来源含 bilibili 时做登录 409 复验，显式空选择返回 409 `no_sources_selected`；旧客户端（不传 `sources`）行为不变。
- 修复插件面板打开后连接徽章误显「未连接」数秒：徽章活性与就绪探测解耦——后端新增纯活性端点 `GET /api/ping`（无 DB / provider 探测，降级模式亦放行），popup 连接徽章（`checkBackendStatus`，3s 超时）与 service worker 的 WS 前置探活（2s 超时）改打 `/api/ping`，404（旧后端）时回退 `/api/health`。原先两者都等 `/api/health`，而 health 同步等一次 embedding 实探（冷缓存实测 6.7s、探测上限 15s）：面板一开撞上冷探测时徽章长时间停在「未连接」，service worker 的 2s 预算还会把健康但冷启动的后端误标掉线（工具栏 `!` 角标误报）。
- 修复稍后再看 / 收藏列表「点移除没反应、要刷新或多点几次才消失」：列表页移除改为乐观更新（共享绑定 `bindSavedCardRemove`）——点击即从列表消失，DELETE 失败时卡片原位恢复、按钮变「重试」并打 `console.error`；稍后再看 / 收藏的增删请求统一加 10s 超时。原实现等响应返回才动 DOM 且 catch 静默吞错：面板打开瞬间同源并发约 80 个请求（每张推荐卡 2 个保存状态 GET + 约 20 个封面 `image-proxy`，缓存未命中单张约 2s）抢 Chrome 单 origin 6 条连接上限，DELETE 被排队数秒，表现即「点了没反应」。真实 Chrome 实测：移除即时消失、后端落库、ping 404 回退路径正常。

## v0.3.117 / extension v0.3.76: SenseNova LLM 探活修复（2026-06-10）

修复 SenseNova 等 reasoning-first OpenAI-compatible 模型在设置页测试与初始化检测里被小输出预算误判为空响应的问题，并发布同步安装包 / 插件包。

- `LLMProvider.health_check()` 不再强制传入极小 `max_tokens`。初始化页 `/api/init-status` 与开始初始化前的 `chat_ready()` 复检都会走该入口，避免模型先产出 `message.reasoning`、尚未到 `message.content` 就被截断。
- 设置页与插件的 `/api/config/probe-service` LLM 测试按钮不再传 `max_tokens=8`，保留 `temperature=0` 与 `reasoning_effort=""`，让可关闭 thinking 的 provider 仍轻量探测，同时兼容 SenseNova 这类 OpenAI-compatible reasoning-first 服务。
- 桌面安装包与插件包 release workflow 的发布步骤改用 GitHub CLI 创建 / 上传 Release 资产，绕过 `softprops/action-gh-release@v2` 在当前 runner 上创建 release 时返回 401 的问题。
- Release 资产上传改为显式 `--repo`、同时暴露 `GH_TOKEN` / `GITHUB_TOKEN`，并逐个文件重试上传，避免多资产上传时单个 zip / 安装包因 `uploads.github.com` 401 中断整次发布。
- 重新打 tag 后若 GitHub 把既有 Release 置回 draft，发布步骤会显式执行 `gh release edit --draft=false`；桌面安装包继续保持 prerelease 标记。
- 桌面安装包 release 创建也加入重试与二次确认，避免长时间打包后在最后的 `gh release create` 受临时 401 影响而丢失已产出的安装器 artifact。
- 后端包版本提升到 `v0.3.117`，浏览器插件版本提升到 `0.3.76`，准备发布 `backend-v0.3.117`、`desktop-v0.3.117` 与 `extension-v0.3.76`。

## v0.3.116 / extension v0.3.75: 惊喜推荐生命周期闭环（2026-06-10）

惊喜推荐的完整生命周期梳理：正向反馈跨重灌保留、浏览过即已读、与普通推荐互斥去重，并用真实 Chrome 端到端验证三端行为。

- 浏览器插件版本提升到 `0.3.75`，发布 `extension-v0.3.75`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.75.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.75-firefox.zip`。
- 修复惊喜推荐「点喜欢后重灌即消失」：v0.3.63 只修了三端会话内保留，但 like 写入的 `feedback_type='like'` 仍被 `get_delight_candidates` 的反馈过滤排除，popup 重开 / `delight.refreshed` 重灌队列时喜欢过的卡片静默消失。现在 `GET /api/delight/pending-batch` 以 `include_liked=True` 查询并对喜欢过的候选下发 `state="liked"`，三端重灌后保留卡片并恢复「已喜欢」展示；显式 `dismiss` / `dislike` 仍即时移出，WS 主动推送 / 候选计数 / CLI 继续排除已喜欢项，不会把喜欢过的内容当新惊喜重复推送。
- 惊喜推荐「浏览过即已读」：`POST /api/delight/respond` 的 `view`（看看/点开浏览）现在会把候选标记为已读（`delight_notified=1`），语义对齐推荐池的 `pool_status='shown'`——当场卡片仍显示「已打开」，但下次队列重灌（popup 重开 / `delight.refreshed`）不再出现，浏览过的惊喜不再永久占据队列。已读标记不重置 4 小时主动推送冷却，看完一条不会推迟下一条新惊喜；`like / chat` 仍保留候选在队列中。
- 惊喜推荐与普通推荐去重：此前同一条内容可以同时出现在惊喜队列和普通推荐流（两边查的是同一个 `content_cache` 池，互不知晓）。现在被惊喜通道认领的行——已作为惊喜送达过（`delight_notified=1`），或当前满足惊喜队列条件（delight 分数 ≥ 阈值且 reason/hook 非空）——会被 `get_pool_candidates` / `count_pool_candidates` 的 servable 闸门统一排除：普通推荐 serve、换一批和「还有 N 条」计数都不会再碰惊喜通道的内容。存储层镜像常量 `_DELIGHT_CLAIM_MIN_SCORE` 由测试与 `DEFAULT_DELIGHT_THRESHOLD` 锁定一致，防止两边阈值漂移产生「夹缝内容」。
- 惊喜推荐浏览器端到端验证 + 三端 view 上报补齐：用隔离后端（临时库 + 种子惊喜候选）驱动真实 Chrome 验证桌面 Web 完整生命周期——喜欢后重载保留并恢复「好，这类多来点。」文案、看看后重灌消失、未操作的一直保留、忽略立即移出，全部通过。E2E 过程中发现桌面 Web 和插件横幅的「去看看」从未调用 `/api/delight/respond` 上报 `view`（移动 Web 端正常），「浏览过即已读」在这两端不生效——已补 fire-and-forget 上报；桌面 Web 的 `normalizeDelight` 同时接住 pending-batch 下发的 `state="liked"`，重灌后恢复已喜欢文案。
## v0.3.115: 自动更新解卡（2026-06-10）

修复「配置页开了自动更新却永远不更新」的静默失效：发布 tag 携带过期 `uv.lock`（版本 bump 时漏跑 `uv lock`），安装侧首次 `uv sync` 即把 worktree 弄脏，所有 git 克隆安装（一键脚本 / AI 安装）的自动更新从装机起被 `dirty_worktree` 守卫永久拦截，且无任何日志或 UI 反馈。

- updater 守卫现豁免仅 `uv.lock` 的脏改动（其他任何脏文件仍然拦截），apply 前先 `git checkout -- uv.lock` 再 `git merge --ff-only`；实测脏安装 0.3.109 → 0.3.114 全链路（GitHub tag 检查 → 快进合并 → `uv sync` → 重启）走通。
- 重新生成 `uv.lock` 并新增 `tests/test_release_consistency.py`：`pyproject.toml` / `openbiliclaw.__version__` / `uv.lock` 三处版本必须一致，发布 bump 漏跑 `uv lock` 时测试直接红，防止再次带脏种子发布。
- `/api/update-status` 与 `/api/runtime-status` 新增 `install_mode`（`frozen` / `git` / `unsupported`）：桌面安装包（PyInstaller 冻结 bundle，无 git 仓库）结构上不支持后端 git 自更新，现在会如实上报而非静默无效。
- 桌面 Web 设置页「自动更新」开关下新增状态行：展示更新状态、阻塞原因（本地化文案，如「代码目录有未提交改动」「本地代码与发布版本分叉」）、当前 / 最新版本与上次检查时间，设置页打开和保存配置后自动刷新；冻结安装包模式下禁用开关并提示「请下载并安装新版安装包」。
- 存量 git 安装升级提示：旧版 updater 代码仍会被脏 `uv.lock` 卡住，无法自动升到本版。在安装目录手动执行一次 `git checkout -- uv.lock && git pull`（或重跑一键安装脚本，会复用现有目录与配置）即可解卡，此后自动更新恢复正常。
- 修复 `/api/sources/status` 小红书状态「永久绿点」：原先只看带 `xsec_token` 缓存行的总数，插件停止同步几周后令牌早已失效（xhs 300031）状态仍显示就绪。现在以 24 小时新鲜窗口判定——窗口内有新发现的带令牌缓存行、或有被令牌回填刷新过 `last_seen_at` 的候选行才算 `ready`，仅剩存量旧行降级为新状态 `stale`（黄点，提示逛逛小红书即可刷新）。
- B 站 cookie 缺少核心登录字段（`SESSDATA`/`bili_jct`/`DedeUserID` 不全）时不再报绿点 `ready`，改为新状态 `partial`（黄点）——绿点不再掩盖「凭据存在但大概率已坏」的情况。桌面 Web 与插件的彩点映射同步新增 `partial`/`stale`，并在状态行可见时每 30 秒自动重拉 `/api/sources/status`（此前只在打开设置页时拉一次，去别的标签页登录平台后回来状态不会变）。


## v0.3.114 / extension v0.3.74: 来源 Cookie 配置对齐（2026-06-10）

插件 side panel 与桌面 Web 配置页的五大来源卡片对齐到 B 站卡片的形态：抖音 / X 也能直接查看并手动粘贴明文 Cookie，状态彩点不再误报，保存配置不会意外清掉已同步的 Cookie。

- 抖音 / X 来源卡片新增明文 Cookie 文本框（插件 + 桌面 Web 同步）：`GET /api/config` 的 `sources.douyin.cookie` / `sources.twitter.cookie` 返回 `resolve_douyin_cookie()` / `resolve_x_cookie()` 解析后的当前凭据（默认脱敏，`reveal_keys=true` 明文）；`PUT /api/config` 把非空值路由到 `data/douyin_cookie.json` / `data/x_cookie.json`（与扩展自动同步同一存储，secrets 不进 `config.toml`），X 粘贴含 `auth_token`+`ct0` 的有效 Cookie 时同时解除 `missing_cookie`/`expired_cookie`/`blocked` 的 re-login 封锁。小红书（token 嗅探）与 YouTube（无需登录）维持差异化说明。
- 插件 side panel 与桌面 Web 的「模型」设置页新增 LLM / embedding 测试按钮：点击会把当前表单草稿 POST 到 `/api/config/probe-service` 做无写入真实连通性探测，LLM 走最小 chat completion，embedding 走 `EmbeddingService.probe()` 绕过缓存取一次向量，结果以 provider / model / latency / error 行内展示，方便保存前确认配置有效。
- 修复 `/api/sources/status` 两处误报：X 的健康表默认行是 `ok`，此前从未跑过 X discovery 时即使没有任何 cookie 也显示「正常，cookie 有效」，现在 `ok` 态会再用 `resolve_x_cookie()` 校验凭据存在，缺失即报 `missing_cookie`；B 站状态此前只看 `config.toml` 镜像，现在回落读 `data/bilibili_cookie.json`（CLI 二维码登录只写文件的场景不再误报「未配置」）。
- `PUT /api/config` 给 `bilibili.cookie` 补上与 `api_key` 同级的防护：脱敏回显（连续 `****`）与空值不再覆盖现有 Cookie；`cookie_env` 空值保留现名。插件 popup 保存时空 Cookie 字段直接省略（对齐桌面 Web 已有行为）。
- 插件 cookie 自动同步的重试 alarm 按平台拆分（`-bili` / `-dy` / `-x`）：一个平台同步成功不再把另一平台刚排的快速重试重置回 60 分钟，登录某平台也只触发该平台的同步；旧共享 alarm 名兼容一轮后清除。
- 配置页 parity 杂项：插件 popup 空字段回退值与后端默认对齐（各源预算 0 = 不限，YouTube 6/50/10、抖音 30/5/30、小红书 30/10 的旧回退移除），预算输入框 placeholder 统一标注「0 = 不限」；桌面 Web `xhsEnabled` 缺省渲染与候选池 `pool_target_count` 回退值（600→300）对齐后端默认。
- 代码组织：`XCookieManager` / `resolve_x_cookie` 迁至 `sources/x_auth.py`（对标 `sources/douyin_auth.py`），`api.app` 保留 re-export 兼容旧导入。

## v0.3.113: Embedding 维度独立配置（2026-06-10）

Embedding 与 chat LLM 的配置边界进一步收紧：embedding 默认目标维度统一为 1024，并显式暴露到配置 API 与桌面 Web 设置页。

- 新增 `[llm.embedding].output_dimensionality`，默认 `1024`，与本地 Ollama `bge-m3` 对齐；设为 `0` 时使用 provider 原生默认维度。Gemini embedding 会传 `output_dimensionality`，官方 OpenAI `text-embedding-3-*` 会传 `dimensions`，Ollama / OpenRouter / 泛 OpenAI-compatible 等未确认支持的后端不传伪参数。
- `EmbeddingService` 的 L2 cache 迁移为 `(text_key, model)` 复合主键，并仅在 provider 确认支持目标维度时按 `model#dim=N` 签名读写，避免同一文本的不同维度向量互相覆盖，同时不把未生效的兼容后端伪装成指定维度。
- 升级影响：既有 Gemini / 官方 OpenAI embedding 用户会从 provider 原生默认维度切到 1024 目标维度；项目当前只把向量持久化在 L2 `embedding_cache.db`，旧 L2 cache 不会被新签名复用，首次推荐/预热会按 1024 重新生成。若确实要继续使用 provider 原生维度，可把 `output_dimensionality` 设为 `0`。
- `/api/config` 与桌面 Web 设置页新增「Embedding 维度」字段。切换 chat LLM provider / model 不会影响 embedding provider / model / 维度，embedding 继续由 `[llm.embedding]` 独立控制。
- 修复 Gemini provider 的 timeout 单位：Google GenAI SDK 使用毫秒，配置里的秒级 timeout 现在会转换后再传入；该修复同时影响 Gemini chat 与 embedding 调用，避免请求被过早超时。

## v0.3.112 / extension v0.3.73: 探针反馈重复推送修复（2026-06-10）

修复用户在安装包/常驻进程场景下点过兴趣探针或避雷探针后，旧探针仍可能从后台推送、画像页或消息缓存里重新出现的问题。

- 探针反馈状态改为原子更新：`discovery_runtime.json` 的正向/避雷反馈历史、短期探索 buffer、probe 冷却 map 与 `last_probe_kind` 都通过进程内锁 + 文件锁 + 临时文件原子替换写入；旧快照保存会和磁盘最新状态合并，不再覆盖用户刚点过的确认/拒绝记录。
- 正向 `InterestSpeculator` 与负向 `AvoidanceSpeculator` 在 `tick/force_tick` 生成前会重新读取最新反馈历史；确认、拒绝和聊天产生的已处理反馈都会进入 novelty guard，避免同一个 domain/specific 被再次生成。runtime 主动推送也只选择 `active` 候选，确认/拒绝后的 stale 探针不会继续被推到前端。
- 插件 side panel、移动 Web 和桌面 Web 统一增加本地 handled probe key：用户点击确认、拒绝或探针内聊后，当前 domain 会立即从 inbox/profile/pending hydration 中隐藏；如果后端返回 stale/`ok=false`，前端只移除旧卡片并刷新画像，不再显示误导性的成功提示。

## v0.3.111 / extension v0.3.73: 图形化初始化入口对齐（2026-06-09）

桌面 Web、安装包首启向导和浏览器插件的首次初始化入口统一到同一套 guided-init 判断与进度流，避免 fresh install 用户被带回命令行。

- 补齐桌面 Web / 安装包首启的图形化初始化入口：`/setup/` 从三步配置向导扩展为「连接 AI → 连接 B站 → 初始化 → 完成」，第 3 步复用 `/api/init-status` / `POST /api/init` / `runtime-stream` 展示来源勾选、前置清单和四阶段进度，不再调用只广播事件的 `/api/init-completed`；`/web` 在 `runtime-status.initialized=false` 且没有推荐数、候选池可用数、待整理数、最近发现 / 补货数等插件同款“初始化后信号”时渲染同款「开始初始化」面板，隐藏示例推荐卡和加载更多按钮，避免后端标记短暂滞后时误回初始化页。补充 Playwright 浏览器流验收（成功进度、前置失败、启动冲突、终态重试、stream 静默 watchdog、PC Web 与插件入口条件对齐）和真实 `/api/init` → `InitCoordinator` → `/api/runtime-stream` 后端契约测试；CI 新增 `web-guided-init-e2e` job（依赖基础 test、缓存 Chromium）后运行。
- 浏览器插件版本推进到 `extension-v0.3.73`，`manifest.json` / `package.json` / `package-lock.json` 版本重新对齐；插件全量测试补齐桌面 Web init 终态刷新断言，确认 `init_completed` 走权威 init status 刷新而不是重复 broad hydration。

## v0.3.110 / extension v0.3.72: macOS 安装包签名封印修复（2026-06-09）

macOS 桌面安装包在无 Apple Developer 账号下改为后处理完成后 ad-hoc 重签，避免 Gatekeeper 把封印失效误报为“已损坏”。

- 修复无 Apple Developer 账号场景下的 macOS 安装包封印失效：PyInstaller 产出的 `.app` 会带 ad-hoc 签名，但构建脚本随后把随包 `ollama` 等资源写进 bundle，导致 Gatekeeper 报“已损坏”。现在 macOS build 在所有 bundle 后处理完成后执行 `codesign --force --deep --sign -` 并立刻 `codesign --verify --deep --strict`，DMG 打包前保证 `.app` 至少处于内部自洽的 ad-hoc 签名状态；文档和 Release 文案同步补充可信来源下的 `xattr` / 本机重签处理命令。仍未做 Apple Developer ID 签名 / notarization。

## v0.3.109 / extension v0.3.72: 配置页对齐与统一来源接入状态（2026-06-09）

桌面 Web 配置页补齐到与插件设置页同等的可配置面，五大来源新增统一的「接入状态」彩点，并修复一个 `GET /api/config` 漏返回的字段。

- 桌面 Web 设置页与插件设置页对齐：补齐此前只在插件暴露的配置项——模型 tab 的 `llm.concurrency` 与 DeepSeek `reasoning_effort`；平台源 tab 的完整 X(Twitter) 源块 + `GET /api/sources/x/status` 源健康提示、YouTube `min_interval_minutes`、候选池 X 占比；调度 tab 的 9 个真实 runtime 参数（断开宽限 / 刷新轮询 / 行为触发阈值 / 反馈积累阈值 / 热门 + 探索刷新小时 / 单轮发现上限 / 主动推送轮询 / 猜测器空闲检查）；通用 tab 的局域网访问密码与开机自启（复用 `/auth/admin`、`/autostart/apply`，桌面 Web 同源 loopback 视为可信本机）。同时移除桌面 Web 仍残留、runtime 已不消费的 `discovery_cron` 旧字段，与插件保持一致（后端 `[scheduler].discovery_cron` 兼容字段保留）。
- 修复 `GET /api/config` 漏返回 `scheduler.feedback_batch_threshold`：该字段 `config.py` 有、`PUT /api/config` 也接受，但 `SchedulerConfigOut` 漏了它 → 插件端与 web 端的「反馈分析积累阈值」都显示空、保存会被静默重置为默认 3。现补进响应模型与构造逻辑。
- 新增统一来源接入状态：新后端端点 `GET /api/sources/status`（`SourcesStatusResponse`）用纯本地信号（B站 cookie 登录字段、抖音 cookie 文件/环境变量、带 `xsec_token` 的小红书缓存条数、X 实时健康存储）给每个来源给出一致的登录 / cookie 状态，不发任何对外平台请求。桌面 Web 设置页平台源 tab 顶部新增「来源接入状态」彩点列表，插件设置页每张来源卡片也加上同款状态行——五个来源（B站 / 小红书 / 抖音 / YouTube / X）现在都像原来只有 X 那样直观展示登录态。诚实标注：只有 X 是实时校验的「正常」，其余按本地 cookie/令牌是否就绪显示「就绪 / 未配置」，YouTube 标「公开源 · 无需登录」。

## v0.3.108 / extension v0.3.71: X（Twitter）内容源接入（2026-06-09）

第六个内容源 X（Twitter）：服务端 cookie 重放发现 + 浏览器扩展互动捕获 + `init` 历史偏好回填，源健康 / 配置 / 设置页全链路与既有源对齐。

- 新增 X 发现源（`source_platform="twitter"`，标签「X」）：`XAdapter` 服务端 cookie 重放，分发 `search`（画像关键词）/ `feed`（For-You）/ `creator`（账号订阅）三策略，经 `x_normalize.normalize_tweet()` 转 `DiscoveredContent`（`content_type ∈ {tweet, thread}` + `body_text` 全文）入统一候选池；后台 `XDiscoveryProducer` 按预算 + 源健康调度。`twitter-cli`（可选 extra `openbiliclaw[x]`，Apache-2.0，自带 curl_cffi TLS 指纹）全程只读、lazy import（`enabled=false` 路径绝不 import）。
- 扩展侧：MAIN-world GraphQL tap 把用户在 x.com 的点赞 / 转推 / 回复 / 打开推文 / 关注捕获为 `BEHAVIOR_EVENT`；登录 x.com 后自动把 `auth_token`+`ct0` cookie 同步到后端供服务端重放；X 文本卡片渲染并将 `body_text` 透传进 LLM prompt。
- `init` 历史偏好回填：`openbiliclaw init`（CLI + 图形化）新增拉取用户**自己的** X 点赞 / 收藏（`XClient.likes()` / `bookmarks()`，底层 `fetch_user_likes` / `fetch_bookmarks`），转成 `like` / `favorite` 事件喂画像——与 B 站收藏回填同一通路；X 无扩展任务、服务端直拉、cookie 未同步时静默跳过。
- 新增 `openbiliclaw fetch-x` 命令：独立触发 X 点赞 / 收藏拉取（对应 `fetch-xhs` / `fetch-douyin` / `fetch-youtube`），`--dry-run` 只看不写，不需 daemon。
- 修复 X 源健康恢复死锁：`missing_cookie` / `expired_cookie` / `blocked` 这类 re-login 状态原本无法自动恢复（`is_ready()` 会永久 park 住 producer），现 `/api/sources/x/cookie` 收到有效 cookie 即调 `XSourceHealthStore.clear_relogin_block()` 解封——cookie 过期重登后发现能自动续上。
- 修复设置页 X 开关：`PUT /api/config` 之前静默丢弃 `sources.twitter`、`GET /api/config` 也不返回它 → 设置页开关存不下、刷新即丢；现补齐 `TwitterSourceConfigOut` + `update_config` 的 twitter 分支，X 启用开关与候选池 X 占比端到端持久化。
- 配置：`config.toml` 的 `[sources.twitter]`（enabled / mode / cookie_env / 预算 / 间隔）与 `[scheduler.pool_source_shares].twitter` 全链路读写；`init` 平台清单纳入 twitter。
## v0.3.104 / extension v0.3.69: Windows 安装包版本元数据修复（2026-06-09）

- 修复 Windows 安装包 / 主程序版本属性不完整：`OpenBiliClaw.exe` 现在由 PyInstaller 写入 `FileVersion` / `ProductVersion` / `OriginalFilename` 等 VERSIONINFO 资源，Windows 资源管理器、任务管理器和诊断脚本都能看到正确版本。
- 修复 Inno Setup 安装器自身 `FileVersion` 为空的问题：CI 会传入纯数字四段 `VersionInfoVersion`，同时保留展示用 `ProductVersion` / `DisplayVersion`，带 commit stamp 的手动 artifact 也不会写坏 PE 数值版本。
- `release-desktop.yml` 与手动 `build-installers.yml` 均同步传递版本元数据，避免自动发布包和手动构建包版本显示不一致。

## v0.3.103 / extension v0.3.69: 桌面安装包运行体验修复（2026-06-09）

- 修复 Windows 桌面安装包推荐流在低库存 / 空库存时的卡顿与“突然整批换内容”：`/api/recommendations/reshuffle` 与 `/append` 在可用池为 0 时立即返回空列表，并通过后台任务 + 30 秒防抖触发补货，不再让用户滚动交互等待补货链路。
- 修复桌面 Web 图片加载慢：追加推荐卡片先渲染，再异步预热封面；首屏 delight 封面改为 eager/high priority/async decode，避免原生 lazy loading 拖慢第一屏观感。
- 修复安装包升级后仍像“没更新”的静态资源缓存问题：`/web` 与 `/web/` 动态注入 CSS/JS `?v=` 指纹，并返回 `Cache-Control: no-store`，确保新安装包打开的是新前端代码。
- 补充回归测试覆盖空池补货、推荐引擎空候选短路、桌面 Web 图片加载优先级与静态资源 cache-bust；桌面安装包由 `desktop-v0.3.103` tag 触发自动发布。

## v0.3.102 / extension v0.3.69: 图形化引导初始化（GUI guided init）（2026-06-07）

- 统一桌面安装包与 AI / 脚本安装的用户数据目录：打包版默认改用 `~/OpenBiliClaw` / `%USERPROFILE%\OpenBiliClaw`，与一键安装共用 `config.toml`、`data/`、`logs/`；旧安装包写在 `~/Library/Application Support/OpenBiliClaw` / `%LOCALAPPDATA%\OpenBiliClaw` 的数据会在首启时非覆盖拷贝到统一目录。若用户先运行安装包、后运行一键脚本，脚本现在能在已有用户数据目录里补齐源码 checkout，不再因目录非空失败。
- README 用户交流群区块新增微信用户群二维码入口，并保留原 QQ 群二维码，方便用户按常用平台加入社区。
- PC Web 顶部新增 GitHub Star 强引导：复用插件的 GitHub-Buttons 风格，显示“好用求 Star”入口并缓存实时 star 数，点击跳转项目仓库。
- 新增 Chrome Web Store 商店页文案源 `docs/chrome-webstore-listing.md`：补齐项目主页、GitHub 项目页、Releases / AI 部署说明、插件安装使用步骤、后端依赖、本地优先隐私说明和提交前检查清单；`docs/index.md` 与插件模块文档同步挂入口，避免商店公开页只剩短概述、缺少安装和使用引导。
- 修复桌面安装包入口忽略 `config.toml [api].host` / `[api].port` 的问题：打包版现在与 `openbiliclaw start` 一样默认按配置监听（默认 `0.0.0.0:8420`，手机 `/m/` 可达），仍保留 `OPENBILICLAW_HOST` / `OPENBILICLAW_PORT` 作为显式环境变量覆盖。
- 抽出共享异步初始化流水线 `cli.run_guided_init`：`openbiliclaw init` 的四阶段（拉取 + 入库 / 分析偏好 / 生成画像 ‖ 发现补池）原先内联在 CLI 命令里、被四处独立 `asyncio.run` 包着，无法被后端复用。现在合并为一个协程，CLI 用单次 `asyncio.run(run_guided_init(...))` 驱动、后端在服务事件循环里直接 `await`，互不嵌套 loop。bootstrap 采集器仍是同步实现但改走 `asyncio.to_thread`，不冻结 API loop；唯一与路径相关的发现补池步骤以 `discover_backfill` 注入（CLI 传一次性引擎、后端传持锁的 `controller.run_init_backfill`）。CLI 行为 / 输出 / 退出码零回归。
- 新增 `InitCoordinator`（`runtime/init_coordinator.py`）+ `init_runs` 持久化状态机（`storage/database.py`）：单飞启动用 `BEGIN IMMEDIATE` CAS 预定（TOCTOU 收口在 DB），单写者串行化状态写入 + 进度事件（`_write_lock` 保证并行 stage 3/4 的 `sequence` 不丢更新），协作式取消，启动 reconcile 把崩溃残留的 `starting/running` 行判失败，避免 `/api/init-status` 永远报 running。
- 新增 `GET /api/init-status`：权威进度 + 前置清单（B站登录 / LLM / embedding / 已启用平台 + `is_profile_ready`），远程可读、降级可读、远程 `can_manage=false`；前置探测 `InitPrereqs`（`runtime/init_prereqs.py`）TTL 缓存 + 单飞，避免轮询打爆 chat provider / `validate_cookie`。LLM / embedding 改为严格真实探测：各发一次最小真实请求，超时 / 失败一律判未就绪（不再乐观放行 —— 让“状态检查通过”真正代表服务可用），成功 / 失败分别用长 / 短 TTL 缓存（修好后能快速复检），probe 全程经 `asyncio.gather` 并发以压低首检延迟。
- 新增 `POST /api/init` + `POST /api/init/cancel`（仅本机）：占坑前先做廉价拒绝（`unsupported_runtime` / `already_initialized`），再 `try_start` 单飞、临界区内复验前置（缺则复位 idle、不留 stuck `starting` 行），经任务注册表后台跑 wrapper；wrapper 是唯一状态 / 事件写者，终态落 `completed/failed/cancelled` 并发 `init_progress/completed/failed` 事件。
- init 期间写者门控（deny-by-default）：中间件对所有 `POST/PUT/PATCH/DELETE` 默认返回 `409 init_running`，仅放行 init 必需路径（`/api/init(/cancel)`、`/api/bilibili/cookie`、`/api/auth/*`、精确段匹配的 `/api/sources/<src>/{kick,task-result}`）；两个有副作用的 GET 另行门控（`/api/recommendations` 空历史 bootstrap serve 跳过、`/api/sources/*/next-task` 只派发 init-owned 任务）；后台循环经 `background_llm_work_allowed()`（account_sync / startup）+ `ContinuousRefreshController` 注入的 `init_active_check`（连续 refresh / soul / producer）全部暂停；`/api/bilibili/cookie` 同值 no-op / 异值 409；`/api/sources/*/task-result` 放行但 init 期跳过池写、仅对 init-owned 结果 propagate 且跳过增量画像管线；init 任务豁免热重载取消（`cancel_all(exclude={"guided_init"})`）。整套门控经 9 轮 Codex 对抗验收收敛至 PASS。
- 插件推荐 tab 引导初始化：未初始化空状态不再叫用户去命令行，而是给一个「开始初始化」按钮（点击驱动校验：点击时置「检查中…」加载态并实时拉 `/api/init-status`，前置未通过则展示前置清单 + 原因、按钮复位、不启动初始化；全通过才启动，避免空等一个上来就慢的预检）+ 启动后进度条（订阅 `runtime-stream` 的 `init_progress/failed/completed` + 3s 轮询兜底，完成自动加载推荐 / 画像）；DOM 无关逻辑抽到 `popup-init-control.js` 并单测。画像 / 画像编辑空状态文案改为指向推荐页初始化。
- 引导初始化按数据来源勾选：「开始初始化」面板新增平台来源勾选（B 站为必选基座、勾选禁用；小红书 / 抖音 / YouTube 可选，默认不勾），并配文案提示「使用某平台前需在当前浏览器登录该平台账号、未在设置开启的平台先去设置开启」。复选框静态渲染（idle 面板秒开，不引入慢探测）；点击时按 `/api/init-status` 的 `enabled_platforms` 校验：勾了未开启的平台会提示去设置而非静默跳过。`POST /api/init` 新增可选 `sources` 入参，后端经 `_select_init_platforms` 把选择收窄为 `选择 ∩ 配置已开启`（无法初始化未配置的来源），B 站恒为基座；不传 `sources` 时维持「用全部已开启平台」的旧行为（CLI 路径不变）。前后端各补单测（`init-control.test.ts` 来源勾选 / 需开启判定、`test_api_app.py` `_select_init_platforms` + `sources` 驱动 include 开关）。
- 插件头部窄宽度对齐修复 + GitHub Star 按钮：side panel 默认窄宽（<460px）下头部不再把操作图标挤到品牌下方、右浮成空一截的第二行；改为**始终单行**布局（品牌左、图标右、整体垂直居中），窄屏隐藏装饰性 eyebrow、状态徽标仅在空间不足时紧凑换到标题下、图标压到 28px，宽屏（≥460px）维持原样（含修复 `.webui-button{width:32px}` 因源码靠后盖过 `@media` 压缩规则的坑，改用 `.hero-actions button` 提高优先级）。Star 引导做成大项目常见的 **GitHub-Buttons 双段样式**（`[🐙 Star | 数量]`）：Octocat + 「Star」动作块 + 实时 star 数小盒，右对齐放在功能键图标列的**下一行**（`.hero-sub` 内，与 hero 文案同排靠右）。star 数由 popup.js 拉 `api.github.com/repos/...`（CORS `*`，无需加 host 权限）并 `localStorage` 缓存 12h；失败 / 限流则只显示 `[🐙 Star]`。点击仍是**打开仓库** —— 直接点 star 必须带 GitHub OAuth / 会话认证，连 GitHub 官方 star 按钮组件也只是跳转，故不做。用 chrome-devtools 在 360 / 400 / 560px 实测三档、窄宽不与文案重叠；`tests/popup-layout.test.ts` 改判 GitHub-Buttons 样式 Star 按钮（含 count 拉取）断言。
- 真号端到端验证：隔离数据目录跑真 B站 Cookie + 真 LLM + 本机 ollama embedding，CLI `openbiliclaw init` 与 API `POST /api/init` 均退出码 0 / `completed`、画像生成、发现项落 `content_cache`，`sequence` 在并行 stage 3/4 下严格递增。
- 桌面安装包升级修复（接 v0.3.101 桌面打包）：Windows 重装 / 升级不再因旧实例占用文件报 “files in use” —— `packaging/openbiliclaw.iss` 加 `CloseApplications=force` + `[Code] PrepareToInstall` 在拷贝文件前 `taskkill /T /F` 强制关闭运行中的 OpenBiliClaw 进程树（含其拉起的 ollama），并留 0.8s 让句柄释放；同时把用户数据从安装目录迁出，升级不再锁库、卸载不再误删画像，旧版遗留在安装目录的 `config.toml` / `config.local.toml` / `data` / `logs` 首启自动迁移（幂等、不覆盖已有、移动失败降级为留在原地不崩）。新增 `tests/test_packaging_entry.py` 覆盖跨 OS 数据根解析、onedir 安装目录 / 数据目录分离与迁移各分支。
- 桌面应用改为**托盘常驻(Windows + macOS 对齐)**:打包从控制台程序改为窗口化(`openbiliclaw.spec` `console=False`),启动后不再弹命令行窗口 —— Windows 常驻右下角系统托盘、macOS 常驻右上角菜单栏(`.app` 设 `LSUIElement=true` 做无 Dock 的菜单栏代理)。uvicorn 跑在后台线程、`pystray` 托盘图标占前台主线程,右键菜单含「打开 Web 界面 / 查看运行日志(弹实时 tail:Windows PowerShell 控制台、mac Terminal `tail -f`)/ 退出 OpenBiliClaw」,关掉任何窗口都不停后端,只有菜单「退出」会优雅停服(`server.should_exit`)。窗口化无 stdout,故 `entry.py` 首启即把 stdout/stderr 重定向到 `logs/desktop.log`(`print` 不再崩)、`__main__` 兜底把异常写 `logs/crash.log`。托盘门控 `_should_use_tray`:frozen + (`os.name=="nt"` 或 `sys.platform=="darwin"`) + pystray 可用,其它平台 / dev 维持前台 server;`spec` 按平台打包(Windows: pystray + Pillow;macOS: 另加 pyobjc Foundation/AppKit/Quartz;Linux 排除 pystray)。`_resolve_runtime_paths` 新增尊重预设 `OPENBILICLAW_PROJECT_ROOT`(便携 / 多实例 / 隔离测试)。`pyproject` packaging extra 加 `pystray`/`Pillow`/(darwin)`pyobjc-*`,CI 两端统一 `pip install -e ".[packaging]"`。**macOS 已在本机端到端实测**:隔离数据根 + 8499 端口跑打包 `.app`,`/api/health` 200、进程在 tray loop 下常驻、stdout 落 `desktop.log`、无 crash、`LSUIElement` 生效、真实用户数据未被污染;Windows 由 CI 验证可打包,托盘交互待真机确认。`tests/test_packaging_entry.py` 加托盘门控 + 日志重定向 no-op + `OPENBILICLAW_PROJECT_ROOT` override 断言。
- macOS 安装包补 **Intel(x86_64)支持**:真机实测发现 CI 原只产 arm64 `.dmg`,Intel mac 装会报 `incorrect executable format`。`build-installers.yml` 的 mac job 改为矩阵双架构原生构建(`macos-14` → arm64、`macos-13` → x64,各自打包对应架构的 ollama + wheels;universal2 因 ollama 为单架构二进制不可行),产物拆为 `openbiliclaw-macos-installer-arm64` / `openbiliclaw-macos-installer-x64`,`.dmg` 文件名带架构后缀。Apple 芯片与 Intel mac 均可安装。
- 修复 embedding 缓存 SQLite 跨线程崩溃:后台 discovery 候选后处理(`_normalize_topic_groups`)与推荐预热(`prewarm_supergroup_embeddings`)在 worker 线程读 L2 缓存时报 `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread`,导致发现/推荐池静默变质(健康检查仍 OK)。`EmbeddingCache` 改为 `check_same_thread=False` + `RLock` 串行化所有连接操作(主 `Database` 早已 `check_same_thread=False`,故仅此缓存受影响);新增跨线程回归用例。
- CI 维护:升级所有工作流的 GitHub Actions 到 Node 24 版本(`checkout@v6`、`setup-python@v6`、`setup-node@v6`、`upload-artifact@v7`、`download-artifact@v8`),清掉 "Node.js 20 actions are deprecated" 弃用告警(6/16 起强制 Node 24、9/16 移除 Node 20)。`windows-latest` 自动迁移到 `windows-2025` 镜像,无需改动。
- 修复 Windows 托盘应用 ollama 子进程弹控制台窗口:`ollama serve` 原用 `DETACHED_PROCESS`,使其子进程 `ollama runner` 没有可继承的控制台、转而**自己分配可见 conhost 窗口**(用户看到的"命令行一闪一闪"像在重启)。改用 `CREATE_NO_WINDOW`——给 serve 一个隐藏控制台、runner 继承之,两者都不弹窗(主应用 `console=False` 已在托盘改造中修过)。**真机复测仍有窗口闪 → 进一步定位为 ollama 自己 spawn 的 runner(`llama-server.exe`)用 `CREATE_NEW_CONSOLE` 起、有独立 conhost,`serve` 的 flag 管不到。** 随包 ollama 顺带从 `0.18.2` 升到 `0.30.6`(但 0.30.6 实测仍弹),**真解是把随包 `ollama.exe` + `lib/` 下所有 runner exe 的 PE 子系统从 console(3) 改成 GUI(2)**(`packaging/patch_pe_subsystem.py`,打包后跑)—— GUI 子系统的 exe 永不分配控制台,无论谁用什么 flag 起它都不弹窗 / 无 conhost,且不影响 stdout/stderr 管道与 runner HTTP 端口。`tests/test_patch_pe_subsystem.py` 覆盖 console→GUI 翻转 / 已 GUI 不动 / 非 PE 跳过。
- 安装包版本号打上 commit SHA:`build-installers.yml` 把版本后缀加上 `${GITHUB_SHA:0:7}`(如 `0.3.102-guiinit.f1f2b38`),写进安装包文件名 + Windows「程序和功能」显示的版本。以前每次构建都叫 `0.3.102-guiinit`、无法分辨装的是哪版代码;现在一眼可核对所装即所编。
- 桌面应用**单实例**:装好后多次点图标(或自启动 + 手动启动并发)不再开多个后端/托盘 —— `entry.py` 首启取 OS 级文件锁(`openbiliclaw.lock`,Windows `msvcrt.locking` / 类 Unix `fcntl.flock`,进程退出或崩溃由系统自动释放,无 stale 锁,优于 PID 文件)。锁忙时第二次启动直接打开现有实例的 Web 界面并退出,不再起新后端。锁按数据根目录隔离(便携 / 多 profile / `OPENBILICLAW_PROJECT_ROOT` 覆盖可并存)。仅 frozen 包启用(dev 不限)。已在 mac 真实 frozen app 双启动实测(实例 2 退出、实例 1 续服、端口仍只一个后端);Windows `msvcrt` 路径同逻辑,由单测 + 真机确认。`tests/test_packaging_entry.py` 加锁互斥 / 跨目录可并存断言。
- 修复封面加载慢(图片代理不读缓存):`/api/image-proxy` 原来每次都先去上游重拉图,只有上游失败才读本地缓存,已缓存的封面也要 ~2s。改为**缓存优先** —— 命中直接回本地文件(`X-Image-Cache: hit`),未命中才下载 + 缓存(`X-Image-Cache: miss`,慢 miss 记 debug 耗时日志);同一 URL 第二次起从磁盘秒回。`tests/test_api_image_proxy.py` 加缓存优先回归用例(清空上游后第二次仍 200 + hit)。
- 修复图片缓存写到安装目录:`image_cache._CACHE_DIR` 原是相对路径 `data/image-cache`,按进程 CWD(打包后 = 只读安装目录)解析,导致缓存落在 `…\Programs\OpenBiliClaw\data\image-cache`。改为按配置 `Config.data_path`(尊重 `OPENBILICLAW_PROJECT_ROOT` / `data_dir`)解析并缓存一次,落到统一用户数据目录(`~/OpenBiliClaw/data/image-cache` / `%USERPROFILE%\OpenBiliClaw\data\image-cache`)。安装目录里的旧缓存可重建,不迁移。
- 修复 Windows 打包版推荐空池 / 低库存运行时体验:`/api/recommendations/reshuffle` 与 `/append` 在 `pool_available_count=0` 时立即返回 `items=[]`,不再读取画像或进入推荐引擎昂贵路径,并通过 30 秒 debounce 只触发一次自动补货;`RecommendationEngine.serve()` 在可用池为 0、或候选被 `excluded_bvids` / 最近已看过滤到 0 后直接返回,跳过 curator scoring、MMR embedding 与推荐历史写入。托管 Ollama 启动默认给子进程传 `OLLAMA_KEEP_ALIVE=24h`(保留用户显式值),减少 `bge-m3` / `llama-server` 在 UI 请求间隔中反复卸载冷启动。新增 API / recommendation / ollama supervisor 回归测试。
- 修复桌面托盘应用三处运行期问题(接随包 ollama 治理):**① 后端静默退出无迹可循** —— 托盘版 uvicorn 跑在 daemon 线程,线程内未捕获异常会静默终结(`__main__` 兜底只看主线程),结果托盘还在、后端已死且无 `crash.log`;`entry.py:_run_server_in_tray` 现在给线程目标包一层 try/except,异常写 `logs/crash.log` + 打印,后端线程崩溃不再无声。**② 托管 ollama 退出后变孤儿** —— `_ollama_start_serve_background` 原先丢弃 `Popen` 句柄,托盘「退出」只停后端不停 ollama,留下孤儿 `ollama serve` + `llama-server` runner(与 Windows 事件日志里 `llama-server.exe` 触发 `RADAR_PRE_LEAK_64` 资源泄漏告警吻合);新增 `runtime/ollama_supervisor.stop_managed_ollama()`,只停**本进程亲手拉起**的 ollama(整棵进程树:Windows `taskkill /T`、类 Unix 进程组 `SIGTERM`),对**外部已在运行、被我们复用**的 ollama(句柄为 `None`)一律不动,`entry.py` 在托盘退出 `finally` 调用它,clean quit 不再留孤儿。**③ 日志中文乱码** —— 打包版绕过 `openbiliclaw start`、从不调 `configure_logging`,输出只裸落 `desktop.log`、无结构化 `openbiliclaw.log`,且 `desktop.log` 是无 BOM 的 UTF-8,Windows 中文区查看器猜成 GBK → 乱码;现在首启 best-effort 调 `configure_logging(..., sweep_unmanaged=False)`(拿与 CLI 同款的轮转 UTF-8 `openbiliclaw.log`,不误删运行中的活跃 `desktop.log`),并在**新建** `desktop.log` 时写 UTF-8 BOM(追加旧文件不重复写)。`tests/test_ollama_supervisor.py` 加托管句柄记录 / 复用不记录 / 停树发进程组信号 / 幂等断言,`tests/test_packaging_entry.py` 加新建写 BOM / 追加不重复 BOM 断言。注:`RADAR_PRE_LEAK_64` 是 ollama 自带 `llama-server` 退出时的堆资源回收行为(其二进制内部、非本项目代码),清理孤儿可减少残留;B站搜索限流属站点反爬的外部因素。
- 桌面应用启动加「启动中」反馈(解决"点了没反应"):窗口化托盘应用从双击到托盘图标出现,要等 Python 启动 + 本机 Ollama 预检(最长约 15s)+ 后端装配,这中间没有任何窗口/提示,用户以为没点上、反复点。**Windows** 接入 PyInstaller 原生启动闪屏 —— `packaging/make_splash.py` 在打包时生成 `build/splash.png`(有 CJK 字体渲染中文「正在启动,请稍候…」、否则降级英文,生成的 PNG 不出豆腐块),`openbiliclaw.spec` 仅 Windows 接 `Splash` 目标;exe 一启动(Python 还没加载)就由 bootloader 在 OS 层画出闪屏,`entry.py` 在托盘图标即将出现时 `_close_splash()` 无缝关掉,selftest / 单实例 busy / 前台回退 / 启动崩溃各路径也都会关闭、绝不卡屏。**macOS** 因 PyInstaller 闪屏不支持(菜单栏代理又无 Dock 弹跳),改在启动早期 `_notify_starting()` 发一条系统通知。Splash 接入对 PIL / `Splash` 缺失做降级(打不出闪屏也不让构建失败)。`tests/test_make_splash.py` 验证生成合法 PNG / 尺寸 / 自动建目录,`tests/test_packaging_entry.py` 验证 `_close_splash` 无 `pyi_splash` 时静默 no-op、`_notify_starting` 仅在 frozen+darwin 触发。
- README 首屏转化优化：中英文 README 改为「10 秒看懂 / OpenBiliClaw in 10 Seconds」Hero + 快速开始 CTA，首屏直接展示 Chrome Web Store 安装、AI 助手部署后端与 Star 支持；用户交流群、最近更新、隐私速览下移到功能预览 / 页尾之后。新增 `docs/images/hero-demo-zh.gif` / `hero-demo-en.gif` / `hero-demo.gif` / `hero-demo.png`，由 `scripts/build_readme_hero_demo.py` 复用现有桌面 / 移动端截图生成四步 storyboard（跨平台信号、本地画像、推荐理由、反馈调教），不引入外部素材。
- 修复插件窄宽下「开始初始化」按钮被裁剪且滚不到:`.empty-state` 卡片在 `.view`(`flex: 1`)的弹性列里会被压缩,叠加自身 `overflow: hidden` 把底部「开始初始化」按钮裁掉;而压缩后 `.view` 恰好填满 `.content`,导致没有可滚动的溢出 —— 短 / 窄视口下按钮既看不到、也滚不到。给 `.empty-state` 加 `flex-shrink: 0` 固定自然高度,卡片改为溢出进 `.content` 滚动区,按钮恢复可滚动可达。用真实 Chrome(chrome-devtools)在窄视口实测:修复前 `scrollable=false` / 按钮被裁 149px,修复后 `scrollable=true` / 滚到底按钮完整可见。`tests/popup-layout.test.ts` 加 `.empty-state { flex-shrink: 0 }` 断言防回归。
- 修复 macOS 托盘「查看运行日志」点了没反应:旧实现用 `osascript … tell application "Terminal"` 拉起实时 tail,但这需要 Apple Events 自动化授权,**未签名的打包 .app 会被静默拒绝**,而代码用 `subprocess.Popen` 发射后不查结果、拒绝错误吞掉、兜底也不触发 —— 于是菜单项毫无反应(「打开 Web 界面」走 `webbrowser.open` 无需授权,所以正常)。改为把一段 `tail -f` 写进 `logs/view-logs.command`、用 `open -a Terminal <file>` 以**文档方式**打开(Terminal 直接运行该脚本,无需自动化授权),并用 `subprocess.run` 查返回码,失败才回退到默认应用打开日志文件。`tests/test_packaging_entry.py` 加 mac 分支(写 `.command` + `open -a Terminal`)与失败回退断言。
- README 把**桌面安装包**提升为与「AI 一句话部署」并列的安装方式:中英文 README 快速开始 + 安装与部署详情各新增「下载桌面安装包」一路(macOS `.dmg` / Windows `.exe`,自带本地 embedding、常驻菜单栏/托盘),并写清未签名应用首次打开的 Gatekeeper / SmartScreen 绕过步骤。配套**部分翻转后端 source-only 策略**:后端源码仍 source-only(`backend-v*` 只是 git tag),但桌面安装包二进制改为发布到 **Releases 的实验性预发布**(`build-installers.yml` 的 Actions 产物 ~90 天过期且需登录,无法做文档长期链接;Releases 才耐久免登录),`build-installers.yml` 头注释同步更新说明现状。
- 新增桌面安装包**自动发布工作流** `release-desktop.yml`:推 `desktop-v*` tag(如 `desktop-v0.3.102`)即自动构建 macOS arm64 `.dmg` + Windows `.exe` 并发布为 GitHub **实验性预发布**(`permissions: contents: write` + `softprops/action-gh-release`,内置未签名应用绕过说明),不必再手动 `gh release create`。对标插件的 `release-extension.yml`。Intel x64 `.dmg` 不进自动发布(macos-13 runner 排队过久,会拖垮整次 publish 的门控),仍由手动 `build-installers.yml` 按需产出后补挂;`publish` 用 `if: always()` 保证单条构建腿失败也能把另一条发出去。
- GitHub Pages 项目首页(`docs/index.html`)与 README 对齐:`#install` 区把**桌面安装包**提升为与「AI 一句话部署」并列的安装方式 —— 标题/引导文案改为「下载安装包或交给 AI 部署」二选一,操作区新增「下载桌面安装包」按钮(→ Releases),并加一张「桌面安装包(最省事)」说明卡(自带本地 embedding、托盘常驻、未签名首启绕过)。中英 i18n 同步(`installTitle`/`installLead`/新增 `installDesktop`/`desktopNoteTitle`/`desktopNoteText`),用真实 Chrome 在中英双语下渲染核验按钮与说明卡均正确出现;`docs/index.md` 首页描述同步。
- README 插件安装改为**优先推荐从 Releases 装**:Chrome 应用商店受审核排期影响,版本通常滞后 Releases 几天到一两周,故中英文 README 的「快速开始」与「安装详情」都把「从 Releases 下载最新 `extension-v*` zip 手动安装」列为推荐(最新),Chrome 应用商店降为「省事/自动更新但可能滞后」的备选。GitHub Pages 首页(`docs/index.html`)同步:`#install` 的「Firefox / 手动下载」按钮改为「下载插件 · Releases 最新」,「插件是主要入口」说明卡补充「最新版从 Releases 装、商店可能滞后」(中英 i18n 同步,真实 Chrome 双语渲染核验)。

## v0.3.101 / extension v0.3.67: 开机自启动与本机 Ollama 预检（2026-06-05）

- 新增当前用户作用域开机自启动能力：macOS 写 `~/Library/LaunchAgents/com.openbiliclaw.daemon.plist`，Windows 写 HKCU Run + `openbiliclaw-autostart.pyw`，Linux 写 XDG `~/.config/autostart/openbiliclaw.desktop`；不写系统级服务、不要求 root / 管理员权限，Docker / 未知平台明确返回不支持。
- 新增 `[autostart] enabled/manage_ollama` 配置段，并为 `save_config()` 加入 autostart provenance：普通配置保存默认保留磁盘上的 `[autostart].enabled`，只有 `/api/autostart/apply` 和 `openbiliclaw autostart enable/disable` 以 `autostart_authoritative=true` 权威写入，避免陈旧快照覆盖用户刚切换的登录项。
- `openbiliclaw start` 增加自启动 reconcile：数据库健康后、API 启动前，按当前 LLM / embedding 配置判断是否需要本机 Ollama；只有默认 `localhost:11434` 需要且未运行时才尝试后台拉起 `ollama serve`，远端 / 自定义 loopback 端口只探测不强拉。若 `[autostart].enabled=true` 但系统注册缺失，会在没有 env-managed 配置风险时自动补注册；若 `[autostart].enabled=false` 但系统登录项仍残留，会自动移除该当前用户登录项。
- 新增 API：`GET /api/autostart-status` 远程可读、降级模式可读，返回固定无敏字段；`POST /api/autostart/apply` 仅 trusted-local 可写，带 env / `config.local.toml` shadow / unsupported guard，开启时先写 config 后注册 OS，关闭时先注销 OS 后写 config，失败尽量回滚到操作前状态。
- 新增 CLI：`openbiliclaw autostart status|enable|disable`，并在 `config-show` 中展示开机自启动配置 / 系统注册状态。CLI 与 API 使用同一套 env-managed、shadow 和方向化事务规则。
- 插件 `extension v0.3.67` 设置页通用 tab 新增「开机自启动」开关：打开时读状态，切换时即时调用 apply；不可管理时按 `env_managed` / `shadowed` / `unsupported_*` reason 禁用并展示行内提示。提示明确该开关只影响下次登录拉起后端，不启停当前进程；本机 Ollama 可能随启动预检一起拉起。
- 修复一句话安装默认 LLM 分叉：`config.example.toml`、运行时默认值和 bootstrap 缺省检查统一改为 DeepSeek，避免新装先提示缺 `llm.openai.api_key`、随后又引导用户选择 DeepSeek；自动写入 Ollama embedding 后，bootstrap 状态现在从 config 回读 `ollama/bge-m3`，不再输出空 provider。
- 修复一句话安装复用旧配置时的 OpenAI-compatible 路径：`agent_bootstrap.py` 现在把 `openai_compatible` 作为受支持远程 provider，缺失检查会报告 `llm.openai_compatible.api_key/base_url`，复用旧安装会同步远程 provider 的非空 `api_key/model/base_url`，避免只复用 `default_provider=openai_compatible` 后服务检查失败。
- 人类直接运行 Bash / PowerShell 一行安装脚本时，`agent_bootstrap.py --interactive-confirm` 现在会先收集完整安装向导选项（LLM provider / API Key / base_url / model → embedding → B 站 Cookie → 小红书 / 抖音 / YouTube opt-in）再安装依赖、启动后端和运行 init；选「中转站 / OpenAI 协议兼容服务」会写入 `[llm.openai_compatible]`，AI-agent 非交互 `--llm-preset` 兼容路径保持不变。
- Docker 一行安装显式对齐人类安装向导：`MODE=docker curl ... | bash` 会先收集同一组选项，再启动 compose、同步配置到 `/app/runtime`、等待宿主机浏览器扩展向 `127.0.0.1:8420` 推送 B 站 Cookie 并自动 init；默认 `ollama` embedding 在 Docker runtime 中改写到 compose sidecar `http://ollama:11434/v1`，避免把宿主机 `localhost:11434` 复制进容器。
- 新增桌面安装包打包链路：`packaging/build.py` 现可产出 macOS `.app` + `.dmg`（拖拽到 Applications）与 Windows onedir + Inno Setup `.exe`（`packaging/openbiliclaw.iss`）；把 ~35MB 的 Ollama 二进制打进包（Windows 连 `lib/` runner 一并携带、裁掉 GPU runner），`entry.py` 首启把 `[llm.embedding].provider` 默认翻为 `ollama`（保留模板注释）、注入随包 ollama 到 PATH、跑 loopback preflight 并后台拉取 `bge-m3`，做到本地 embedding 开箱即用；`entry.py` 另加 `OPENBILICLAW_HOST/PORT` 与 `OPENBILICLAW_SELFTEST` 自检。新增手动触发的 `.github/workflows/build-installers.yml`（`workflow_dispatch` 专用，`permissions: contents: read`，只产 Actions artifact——**不创建 GitHub Release、不随 tag 触发**）；后端发布仍维持 source-only 策略。`pyproject` 新增 `packaging` extra（`pyinstaller`）。
- 新增打包应用首启 UI 引导向导：`src/openbiliclaw/web/setup/` 自包含三步向导（① 连接 AI：选 provider + 填 key，写 `PUT /api/config` → ② 连接 B站：装扩展自动同步 + 轮询检测 → ③ 完成：embedding 已就绪打勾，`POST /api/init-completed` 跳 `/web`），`api/app.py` 挂在 `/setup`，`entry.py` 首启自动打开它（不再打开 health JSON）。**同时修复 `web/`（整个网页 UI + 向导）从未进 PyInstaller 包、导致 `/web`/`/m`/`/setup` 在安装版一律 404 的老问题**——`packaging/openbiliclaw.spec` 的 datas 现含 `openbiliclaw/web`。
- 后端源码版本提升到 `v0.3.101`，准备发布 `backend-v0.3.101`；浏览器插件版本提升到 `extension-v0.3.67`。

## v0.3.100: 统一 discovery 待评估池与外站补池预算（2026-06-04）

- 新增 `discovery_candidates` 持久化待评估池和 `DiscoveryCandidatePipeline`：B 站、小红书、抖音、YouTube raw candidates 先统一进入 `pending_eval`，再由共享 evaluator 混源 batch 评估并 admission 到 `content_cache`。来源差异只保留为取数方式、配额和 prompt 上下文，不再各走一套喜好判断流程。
- B 站主 refresh 改用 `ContentDiscoveryEngine.produce_candidates()` 拉 raw candidates；抖音 / YouTube producer 注入 candidate pipeline 后改为 enqueue + drain；小红书被动 notes 和 task-result notes 不再直接写 `content_cache`，而是先进入 `discovery_candidates`，token 回填同时覆盖待评估表和正式池。
- runtime status 新增 `pool_pending_eval_count` 与 `pool_evaluated_pending_count`，`pool_raw_count` / `pool_pending_count` 合并统计待评估 raw candidates；`last_discovered_count` 在 pipeline 路径只统计本轮新入队 raw candidates，已评估候选 retry/admission 不再冒充“新发现”。`pool_available_count >= pool_target_count` 时 `ContinuousRefreshController` 不再 discovery / drain，推荐池上限仍以真实可换数生效。
- `DiscoveryCandidatePipeline` 在 admission 前会优先重试 `evaluated` 待入池候选；若池子在 admission 中途达到上限，剩余高分候选保留为 `evaluated`，下一轮先入池。LLM / provider batch transient、空 / 短 / 长 scores 都会把整批释放回 `pending_eval`，不消耗单条候选 `eval_attempts`；同时递增高阈值 `batch_eval_attempts`，避免永久坏 provider 无限 churn。
- 修复 unified evaluator 验收中发现的边界风险：小红书 observed notes 仍走 mixed evaluator 补主题 / 风格，但 admission 阈值为 0，低分新兴趣不会被丢弃；B 站 / YouTube / 抖音 raw candidates 持久化来源策略 `score_threshold`，抖音 hot/feed 对齐 0.60、search 保持 0.65；pipeline admission 前复用 topic_group / topic_key embedding normalization；成功入池 item 回传给 runtime 更新 `recent_pool_topics`，drain short-circuit 时不会复用旧 topics；`evaluating` crash 遗留行会在启动时过期回收，terminal candidate rows 有 status guard；pipeline 自带共享 drain lock，避免 refresh / XHS / Douyin / YouTube 多入口并发 admission 越过推荐池上限；pipeline 会 clamp evaluator hard-cap，避免超大 batch 尾部未评估候选被 0 分拒绝；franchise quota admission drop 记录为 `rejected_franchise_quota`，非特定 cache admission skip 记录为 `rejected_cache_admission`，不再误报 duplicate；XHS observed enqueue / producer enqueue 使用同一来源 cap helper，按来源 cap 计数包含 `evaluating` 但删除时保护 in-flight 行，并保留 600 条兜底上限。
- `/api/sources/xhs/observed-urls` 响应新增 `enqueued` 字段；`accepted` 只表示本次接收的有效 URL 数，不再把异步待评估内容暗示成已经进入推荐池。
- discovery batch prompt 补充跨平台公平规则，要求模型不得仅因平台来源不同而抬高或压低偏好分；每条候选 payload 带 `source_platform`、`source_strategy`、`source_context`、`content_url`、`author_name`，方便统一 evaluator 在混源 batch 中做可解释评分。
- 后端源码版本提升到 `v0.3.100`，准备发布 `backend-v0.3.100`；浏览器插件版本沿用 `extension-v0.3.66`。
- 修复自动更新版本状态在 runtime 链路中被丢弃的问题：`/api/runtime-status` 的响应模型现在保留 `AutoUpdateService.get_runtime_status()` 合入的 `current_version`、`latest_remote_version`、`last_update_check_at`、`last_update_error`、`backend_update_state` 和 `backend_update_reason`；插件 `normalizeRuntimeStatus()` 同步保留这些字段，避免前端状态归一化浪费后端版本数据。设置页仍使用后端专用 `/api/update-status` 做“版本与更新”展示和手动检查 / 应用。
- 小红书 / 抖音 / YouTube 的 `daily_*_budget` 默认改为 `0`，语义统一为“不设每日上限”；持续补池改为像 B 站一样主要受平台缺口、单轮 `scheduler.discovery_limit` 和 producer 节流控制，避免外站内容被刷完后因当天预算耗尽而长期不补。
- 队列层统一支持 `daily_budget <= 0` 跳过每日上限：`XhsTaskQueue`、`DyTaskQueue` 和 YouTube bootstrap `YtTaskQueue` 都保留正数预算限流能力，但默认不再按天卡死。抖音 hot runtime 预算在配置为 `0` 时不再被缺口动态放大成正数。
- YouTube steady-state producer 在 `daily_*_budget = 0` 时以本轮 `limit` 作为策略执行预算；显式正数仍按 SQLite ledger 做每日剩余额度，便于需要严格限流的用户手动恢复上限。插件设置页、API 配置模型、CLI fallback、`config.example.toml` 和配置参考同步更新。
- 项目主页（GitHub Pages `docs/index.html`）新增 GitHub Star 强引导：顶栏常驻 Star 胶囊按钮 + 结尾专属 Star CTA 卡片，两处均显示实时星标数（GitHub API + sessionStorage 缓存，拉取失败时优雅隐藏、不留占位符，按钮始终可用）；复用页面现有 i18n 实现中英双语 + 响应式，沿用 `--pink` / `--yellow` 品牌色与胶囊按钮风格。纯增量改动，不涉及接口 / 数据流 / 架构。
- 插件已上架 Chrome 应用商店并公开发布（item `cdfjfkdjjhdaccbldipkjhpibnfbiamg`）：README 中英文顶部新增 Chrome Web Store 版本徽章，安装章节改为「商店一键安装为推荐方式 + Releases / 开发者模式作为 Firefox / 手动备选」；落地页 hero / 安装 / 结尾的下载 CTA 改指向商店「添加到 Chrome / Add to Chrome」，Releases 降级为「Firefox / 手动下载」次按钮，中英 i18n 同步。最新插件版本（`0.3.66`）已提交商店审核以从已上架的 `0.3.65` 更新。
- 新增开机自启动 SPEC 与原子化实现计划，锁定跨平台自启动 manager、Ollama preflight、API/CLI/插件设置开关、配置 provenance 与验收矩阵；同步刷新 discovery / recommendation / soul 三张 HTML 架构图，补充真实生产路径、候选池/推荐池数据流和用户画像链路说明。

## extension v0.3.66: 推荐「聊一聊」输入框失焦自动收起（三端）（2026-06-03）

- 浏览器插件版本提升到 `0.3.66`，准备发布 `extension-v0.3.66`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.66.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.66-firefox.zip`。
- 修复「推荐内容点开聊一聊后没法收起」的体验问题：三端内联 composer（桌面 Web `/web` 推荐卡 + 惊喜卡、移动 Web `/m` 惊喜卡、插件 popup 惊喜卡）现在在输入框失焦（焦点离开 composer）后自动收起回原来的操作按钮。根因是桌面 Web 宽屏下 `‹` 返回按钮被 CSS 限定为仅 `@media (max-width:430px)` 可见，展开后唯一退路是 `Esc`（不可见、无人知道）；移动 Web / 插件虽有「再点一次聊一聊」切换但缺少失焦收起。
- 收起是无损的：已输入的草稿在桌面保留于输入框 DOM、在移动 Web / 插件保留于 state `draft` / `chat_draft`，下次展开自动还原。点「发送 / 发出去」时输入框会先失焦，统一用 `relatedTarget` 判断 +120ms 延迟 + 移动 Web / 插件额外的 `sendInitiated` 标志守卫，确保发送照常完成、不被收起抢先；桌面 `autoCollapseComposer` 同时复用于推荐卡和惊喜卡。
- 用真实数据浏览器端到端验证三端：真后端（3331 条真实推荐 + 真实惊喜推荐队列）+ 真 Chrome（插件以 unpacked 加载），逐一验证「展开 → 失焦 → 收起」「带草稿失焦 → 重新展开还原」「输入框聚焦时点发送（先触发失焦）→ 发送照常」三组行为；桌面推荐卡确认 `POST /api/feedback 200`、移动 Web 与插件确认 `POST /api/chat/turns 200` 真实落到后端，未被失焦收起吞掉。
- 同步 `docs/modules/extension.md` 惊喜推荐 composer 行为说明。

## v0.3.99: 桌面 Web 推荐列表不再被池更新冲掉（2026-06-03）

- 修复桌面 Web `/web` 在下滑浏览时推荐卡片会突然整批替换的 bug：根因是桌面前端把「后端推荐池更新」当成了「整页推荐需重新同步」。`web/desktop/assets/js/app.js` 的 runtime-stream 处理器收到 `refresh.pool_updated`（以及后端实际从不下发的 `recommendation.reshuffled`）时会调用 `scheduleBackendHydration()` → `hydrateFromBackend()`，后者无条件执行 `state.videos = normalizeRecommendationList(...)`，用 `/api/recommendations` 的「最新 top 窗口」（`created_at DESC, id DESC`）替换当前列表——把用户「加载更多」追加的历史卡片一并冲掉。此问题在 2026-05-27（`79042ce`）已对插件 popup 和移动 Web `recommend.js` 修过，但桌面 Web（早 5 天于 05-22 创建）当时被漏掉。现在 `refresh.pool_updated` / `recommendation.reshuffled` 不再触发 hydrate，只保留 `config_reloaded` / `init_completed` 这类真·重新水合流程；池子数量 / header 仍由处理器开头无条件的 `applyRuntimeStatus(...)` 更新，用户主动「换一批」/「加载更多」/反馈删除继续各自直接改 `state.videos`，行为与移动 Web、插件对齐。
- 测试：`tests/test_desktop_web_pool_status.py` 新增 `test_desktop_pool_update_does_not_replace_recommendation_list`，断言桌面 hydrate 触发列表不含 `refresh.pool_updated` / `recommendation.reshuffled` 且保留 `config_reloaded` / `init_completed`；扩展端 `runtime-refresh-coalescing.test.ts` 补桌面对称守卫（此前只校验 hydrate 被防抖、未校验 pool_updated 不应触发 hydrate，正是这次 bug 溜过的原因）。同步 `docs/diagrams/web-architecture.html` runtime-stream 合并刷新说明。
- 精简 README 中英文「内容发现引擎」章节:把原本三段实现规格式长文(端点路径、`dy-plugin-*` 源码标签、`source_bootstrap_state.json`、`max(target*2, target+120)` 等内部记账)改写为「安全取数 / 多样性选择 / 候选池计数」三段面向用户的功能介绍,并清理平台表里的 `单源 smoke` / `hot-related` / `discovery producer` 黑话;深层实现细节仍以 `docs/modules/discovery.md` 为准,CN/EN 同步。

## v0.3.98: Ollama 作 chat fallback 时识别修复（2026-06-02）

- 修复「把本地 Ollama 设为 chat 兜底却静默失效」的 bug：`_ollama_is_chat_capable()` 此前只认 `[llm.ollama] model` / `[llm].default_provider` / 模块 override 三个入口，唯独不认 `[llm].fallback_provider = "ollama"`。当用户把全局 `fallback_provider` 设为 `ollama` 但没单独填 `[llm.ollama] model`（常见于本地已用 Ollama 跑 `bge-m3` embedding 的场景），Ollama 会被判为 embedding-only 并被 `_fallback_order()` 从 chat 兜底链里剔除——主 provider 失败时直接抛 `LLMFallbackError`，既不兜底也没有任何告警。现在新增第四个识别入口尊重用户意图（未配 `model` 时用 `llama3` 默认，需本地已 `ollama pull` 对应 chat 模型）；补 `test_ollama_named_as_fallback_provider_is_chat_capable_without_model` 回归，并在 `config.example.toml` 补充 `fallback_provider` 的 Ollama 使用提示。

## extension v0.3.65: Chrome Web Store tabs 权限拒审修复（2026-06-02）

- 浏览器插件版本提升到 `0.3.65`，准备发布 `extension-v0.3.65`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.65.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.65-firefox.zip`。
- 用独立版本重新提交 Chrome Web Store 审核，包内 manifest 不再声明 `tabs` permission，仅保留 `activeTab`、`scripting`、`sidePanel`、`cookies`、`notifications`、`alarms`、`storage` 与受支持平台 / 本机后端 host 权限。

## extension v0.3.64: 保存列表头图与窄宽度头部修复（2026-06-01）

- 浏览器插件版本提升到 `0.3.64`，准备发布 `extension-v0.3.64`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.64.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.64-firefox.zip`。
- 响应 Chrome Web Store `Purple Potassium` 权限拒审：Chrome / Firefox manifest 移除不必要的 `tabs` permission，保留现有 `activeTab`、`scripting`、`sidePanel`、`cookies`、`notifications`、`alarms`、`storage` 与受支持平台 / 本机后端 host 权限；新增 manifest 回归测试防止重新声明 `tabs`。
- 新增 `docs/diagrams/soul-update-flow.html`，用自包含 SVG HTML 梳理 Soul 事件、反馈、对话、探针、手动编辑到五层 OnionProfile 的更新路径，并同步文档导航。
- 修复插件 side panel「稍后」和「收藏」列表无头图的问题：保存列表条目现在归一化封面 URL，按固定 16:9 缩略图展示，并继续通过后端 `/api/image-proxy` 加载平台 CDN 图片。
- 修复插件 side panel 默认窄宽度下顶部工具按钮和左侧标题 / 状态重叠的问题：460px 以下宽度会把 Web、二维码、消息、设置按钮换到品牌区下一行靠右排列。
- 落地后端-only 自动更新首版：新增 `/api/update-status`、`/api/update/check`、`/api/update/apply`，后端 canonical `backend-v*` tag 优先级、prerelease 默认忽略、可信 remote / dirty worktree / fast-forward guard、apply 锁、runtime stream 事件和设置页“版本与更新”入口；插件更新继续交给浏览器商店或 sideload 手动重载。
- 刷新 README 截图与文案：桌面 / 移动端 Web 截图改用真实运行环境的浏览器实拍（桌面首页 / 推荐网格 / 画像+实时看板，移动推荐 / 画像 / 对话），替换 5 月那批已过时的旧图，并同步中英文 README 与现状——移动端底部 Tab 由「推荐 / 画像 / 对话」三个更正为「推荐 / 稍后 / 收藏 / 画像 / 对话」五个、惊喜卡与推荐卡补「稍后再看 / 收藏」动作、桌面卡片描述从「横向双卡片」改为「封面在上的网格」、测试数由 800+/650+ 统一为 1900+；英文 README 补回「用户交流群 / 功能预览截图表 / 更多截图」三块，补技术栈 YouTube 与 Docker 行，并刷新 Roadmap 与局域网访问说明对齐中文。
- 封面磁盘缓存（`data/image-cache/`）新增消费感知定期清理：`content_cache.pool_status` 为 `shown / feedbacked / stale / purged_by_dislike`、且不在收藏 / 稍后再看的封面会被清掉（B 站等 URL 稳定、可重抓来源安全释放空间，实测可回收数百 MB），`fresh` / `suppressed` 与已保存项始终保留；带过期 token、无法重抓的小红书封面默认受保护不删（缓存是其唯一副本），并移除超 30 天的孤儿文件作增长兜底。启动时全量执行、运行时每 6 小时由 `RefreshRuntime._loop_image_cache_cleanup` 增量执行；缓存键与清理逻辑抽到新模块 `openbiliclaw.runtime.image_cache`（`api.app` 复用），新增 `Database.iter_cover_lifecycle` 联表判定保存态。
- 修复小红书封面大面积 502 破图：根因是封面只在「展示时」才懒加载，而小红书签名 URL 的 token 寿命短、等内容被刷出来时多半已过期（实测 775 张中仅 40 张曾被缓存）。新增「发现即缓存」预取——`RefreshRuntime._loop_cover_prefetch` 每 60 秒从 `Database.iter_servable_cover_urls` 取最近 12 小时内仍可展示的封面，`select_prefetch_targets` 把无法重抓的小红书封面排在最前、过滤已缓存 / 非白名单，趁 token 新鲜时落盘（每轮上限 40 张）。同时把 proxy 的白名单 / redirect / 大小校验抽成共享的 `fetch_cover_bytes`（`CoverFetchError`），proxy 路由与预取共用同一抓取核心，避免 SSRF 校验重复实现。

## extension v0.3.63: 惊喜推荐正向反馈保留（2026-06-01）

- 浏览器插件版本提升到 `0.3.63`，准备发布 `extension-v0.3.63`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.63.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.63-firefox.zip`。
- 修复惊喜推荐正向反馈被三端立刻移除的问题：喜欢、收藏、稍后再看、聊一聊和去看看都会保留当前卡片并更新状态；只有不感兴趣、忽略或显式关闭会立即移出队列。
- 补充后端 API、移动 Web、桌面 Web、插件 popup 的回归测试，并用浏览器端到端测试验证桌面 Web、移动 Web、扩展 popup 的正向保留和负向移除行为。

## extension v0.3.62: Chrome Web Store 权限收窄（2026-05-31）

- 浏览器插件版本提升到 `0.3.62`，准备发布 `extension-v0.3.62`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.62.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.62-firefox.zip`。
- Chrome / Firefox manifest 移除 `http://*/*` 宽泛主机权限，发布包只声明 Bilibili / 小红书 / 抖音 / YouTube 内容平台和 `127.0.0.1` / `localhost` 本机后端权限，降低 Chrome Web Store “所有网站权限”深入审核风险。
- 同步隐私政策、README、插件模块文档和设置页提示：商店版默认连接本机后端；局域网 / 远程后端需要带对应 host 权限的开发者构建，或后续补充 `optional_host_permissions` 用户授权流程。
- 收窄 `docs/specs/auto-update.md` 为后端-only 自动更新 SPEC，并同步 README / runtime / extension 文档边界：插件更新不再由后端查询 `extension-v*` 或显示更新横幅，Chrome Web Store / Edge Add-ons / AMO 交给浏览器原生更新，GitHub zip / sideload 保持手动 fallback。

## extension v0.3.61: 插件收藏 / 稍后再看三端对齐（2026-05-31）

- 对齐插件端收藏 / 稍后再看与 PC Web、移动 Web：side panel tab bar 新增独立「稍后」页，推荐卡和 delight banner 都提供「时钟=稍后再看」「星星=收藏」两个互相独立的 SVG toggle；列表移除、推荐卡和惊喜横幅继续共用 `popup-saved-sync.js` 同步同一 bvid 的状态。
- 新增 `popup-saved-surfaces-e2e.test.ts`，以真实 HTTP mock 后端跑插件 `popup-api` 往返，并断言稍后再看 / 收藏互相独立、UI 布线完整；补充真实 Chrome 浏览器端到端冒烟，验证 420px side panel 下五 tab 等宽、无横向溢出、保存按钮选中态和列表移除同步。
- 新增 `docs/specs/auto-update.md`，锁定后端源码自动应用、插件 sideload 自动提示、以及未来商店 / 签名自托管更新通道的边界；明确 `backend-v*` 优先级、更新 API 状态合同和插件不可静默自替换的浏览器限制。
- 新增 Chrome Web Store API v2 上传自动化：`extension/scripts/chrome-webstore-upload.mjs` 可用官方 OAuth refresh token 上传 Chrome-compatible zip，并可选提交审核；新增手动 GitHub Actions workflow `Publish Chrome Web Store Package`，默认只上传不发布。
- 新增 `docs/privacy.md`，补齐 Chrome Web Store 隐私权政策页面：说明插件单一用途、权限理由、处理的数据类型、本地后端数据流、无远程代码、无出售或无关第三方传输。

## extension v0.3.60: 「阿B 最近新记住了什么」改为点击加载更多（2026-05-31）

- 修复画像 tab「阿B 最近新记住了什么」区块过长的问题：该区块的认知卡片此前会随页面滚动到底部**自动续页**（`maybeLoadMoreCognitionHistory` 在 profile 加载后、每次续页结束、以及 `.content` 滚动事件里反复触发），实测会把所有历史认知卡片一次性拉满，使区块无限变长、底部「加载更多」按钮形同虚设。现在改为**纯点击驱动**：首屏只展示最近 3 条，仅当用户点击「加载更多」时才按 `cursor` 分页拉取下一页（每页 3 条），不再随滚动自动续页。推荐列表的滚动自动续页（独立的 `maybeLoadMoreRecommendations` + 意图门控）不受影响。
- 测试：`extension/tests/popup-scroll.test.ts` 新增「画像认知历史仅点击分页、无滚动自动续页」契约用例（断言 `maybeLoadMoreCognitionHistory` 及其全部调用点已移除、加载更多按钮 click 绑定 `loadMoreCognitionHistory`、`.content` 滚动监听不再链式触发认知续页）；扩展端 `node --test` 全绿、`tsc --noEmit` 干净。

## extension v0.3.59: 画像编辑态布局修复（2026-05-31）

- 修复插件画像页编辑态布局不一致：进入「编辑画像」后，side panel 现在会给画像 tab 加 `is-profile-editing` 页面态，用 CSS 强制让只读画像卡片退出布局、编辑面板占据原位置；避免只读内容仍显示、编辑控件跑到整页底部，行为对齐移动 Web 与桌面 Web 的替换式编辑态。

## extension v0.3.58: 收藏 / 稍后再看 toggle 写入中竞态修复（2026-05-31）

- 修复收藏 / 稍后再看 toggle 在「写入进行中卡片重渲染」时被回滚的竞态：新注册按钮发出的状态 GET 读到写入前的旧快照、又在 add/remove 成功之后才返回，会把刚确认的 toggle 盖回旧值。现在 bvid 仍 `busy` 时就丢弃该 hydration（不只看 mutation version 是否 bump），并在写入成功后再 bump 一次版本号让期间发起的 GET 失效；补 `popup-saved-sync.test.ts` 回归。

## v0.3.97 / extension v0.3.57: 局域网密码门禁 + 语义去重就绪探活与横幅修复（2026-05-31）

- 新增局域网 / 远程访问的**可选密码门禁**。配置走新 TOML 段 `[api.auth]`（`ApiAuthConfig`）：`enabled` 总开关、`password_hash`（scrypt）、`session_secret`（HMAC 签名密钥，首次启用自动生成）、`session_ttl_hours`（0=永不过期 / 记住登录）、`trust_loopback`（默认 true，本机免登录、扩展不受影响）、`trusted_proxies` 与 `allowed_bearer_origins`（仅 TOML）。撤销纪元 `auth_epoch` 与密码指纹存 SQLite `auth_state` 表，不进 config。后端为 `auth_core.py`（标准库 scrypt + HMAC 无状态 token + 反代/Origin 解析）+ `api/auth.py`（`create_app()` 内注册的 HTTP 中间件 + `/api/auth/{status,login,logout}` 路由），门禁挡所有其他 `/api/*`（含 `runtime-stream` WS、`image-proxy`），`/api/health` 与静态壳保持公开。
- 凭据默认走 HttpOnly cookie `obc_session`（同源 fetch/img/WS 自动携带），跨源限时 Bearer 为允许列表内的逃生通道；CSRF 对 cookie 鉴权的非安全方法强制 `Origin==Host` + 头 `X-OBC-Auth`。改密 / `--logout-all` / `--rotate-secret` 经 `auth_epoch` 真正撤销所有设备，永不过期登录不会因重启被误撤销。`session_secret` / `password_hash` 永不经 `GET /api/config` 返回。
- 新增 CLI `openbiliclaw set-password`（交互设置 / 修改密码，`--disable` 关闭门禁、`--logout-all` 立即登出所有设备、`--rotate-secret` 轮换签名密钥需重启）。`init` 在「允许局域网访问」后会追加一次「是否设置局域网密码」（默认 No）；`start` 启用时打印 `🔒 局域网访问已启用密码登录`，并在 `trust_loopback=true` 且 `trusted_proxies` 为空时给出反向代理告警。
- 前端登录 UI：移动 Web（`/m`）新增 `views/login.js` + 启动鉴权 gate（`/api/auth/status`、401 触发 `obc:auth-required`），桌面 Web（`/web`）新增登录遮罩 + 同源相对 `/api` base；两端 fetch 带 `credentials` 与非安全方法 `X-OBC-Auth` 头。设计与安全模型详见 `docs/plans/2026-05-30-web-password-auth-design.md`。
- 浏览器插件设置页新增「局域网访问密码」开关：可直接开启 / 关闭门禁并设置 / 修改密码（`popup-auth-control.js` + 设置面板「通用」分区）。后端新增**仅可信本机**的 `POST /api/auth/admin`（插件走 `127.0.0.1` 是可信本机，热生效免重启、改密即撤销旧会话、远程会话即便已登录也 403、env 管理时 409）；`GET /api/auth/status` 增 `env_managed` / `can_manage`。选插件端而非 Web 设置页，是因为插件不会把自己锁在门外。
- `POST /api/auth/admin` 对抗式 review 加固：①写入改为**先持久化 config（快照可回滚）→ 原子 `revoke_and_set_fingerprint`（同事务 bump epoch + 写指纹）→ 再发布运行期门禁**，任一步失败回滚并 503，杜绝旧顺序下「config 写失败却已撤销全部会话 + 污染 DB 指纹」的半状态，两步间崩溃由启动 reconcile 自愈；②改密存的 DB 指纹改用**持久化后的 hash**（`plain=None`，即 `"ph:"+password_hash`），与重启后 reconcile 实际读到的材料一致——此前用明文派生 `"pw:"+明文` 会在下次重启被判为「改密」而误撤销改密后签发的所有会话；③env 管理判定统一到 `config.API_AUTH_ENV_VARS`（覆盖全部 6 个 `OPENBILICLAW_API_AUTH_*`，含此前漏判的 `SESSION_TTL_HOURS` / `TRUST_LOOPBACK`），`/api/auth/admin` 与 CLI `set-password` 写配置路径都按全集 `409` / 拒绝（CLI `save_config` 会写整个 `[api.auth]` 块，任一 env 覆盖都会被烤进文件成为陈旧字面量），并加 `test_api_auth_env_vars_matches_loader_read_surface` 漂移守卫确保该列表与加载器读取面一致；④`/api/auth/admin` 移入中间件白名单、由 handler 自身强制可信本机，对非本机一律 `403 local_only`；`allowed_bearer_origins` 不再被当作可信本机（仅走 token）；⑤**env-managed 写保护下沉到 `save_config` 本身**：`_render_config_toml` 一向把整段 `[api.auth]` 从内存（已被 env 覆盖）的 Config 渲染回文件，于是任何无关的保存（启动期 `session_secret` 生成、`PUT /api/config`、扩展 cookie 同步）都会把 env 值烤成陈旧字面量——现在凡有 `OPENBILICLAW_API_AUTH_*` 在场，被该 env 覆盖的字段改用磁盘原值渲染（且按 loader 的 `_coerce_bool` / 新增共享的 `_coerce_ttl_hours` 归一，否则磁盘上的引号字符串布尔 `trust_loopback = "false"` 会被 `bool()` 写成 `true`、悄悄重开 loopback 免登录）、磁盘无值则整行省略（load 回落默认、运行期仍由 env 治理）；密码凭据特判保留 loader 支持的明文 `password` 键或 `password_hash`（盘上只有 `password` 无 `password_hash` 时也不会在去掉 env 后丢凭据把门锁死），`_coerce_ttl_hours` 还吞掉 TOML 特殊浮点 `nan`/`inf`（`int(nan)` 会抛）不再崩，保护不再只挂在 admin / CLI 两条路径上；⑥`revoke_and_set_fingerprint` 的撤销判定改为**事务内比对指纹**（CAS，比照 `reconcile_password_fingerprint`）：除 enabled 开关 / 显式改密的 `force_bump` 外，只要新指纹与已存指纹不同就 bump——堵住「后台 `set-password` 改了磁盘 hash、admin 无密码 `{enabled:true}` 热发布该 hash 却不 bump，导致旧密码签发的会话在新密码下存活」的窗口；⑦修复通用 env 覆盖切分器把 `OPENBILICLAW_API_AUTH_PASSWORD_HASH` 误拆成 `api.auth.password.hash` 的老 bug——它会往 `auth.password` 注入一个 dict（随后被当作 repr 哈希成废密码）或在盘上已有明文 `password` 字符串时下钻报 `TypeError` 直接起不来；现在 `_apply_env_overrides` 跳过全部 `API_AUTH_ENV_VARS`（这些都由 `_build_api_auth` 显式读取），并把凭据优先级写死为 **env `PASSWORD` > env `PASSWORD_HASH` > 盘上明文 `password` > 盘上 `password_hash`**，`get_auth_plain_password` 在 `PASSWORD_HASH` 当道时返回 `None`（指纹改用 `"ph:"+hash`，不再用已不生效的盘上明文）；⑧修复**非 env 路径**下保存会把盘上明文 `password` 便捷键转成 hash-only、导致重启时指纹基从 `"pw:"+明文` 翻成 `"ph:"+hash` 误判改密、撤销记住登录——`save_config` 现在每次保存都读盘，凭 `verify_password(盘上明文, 内存 hash)` 判断：仍匹配（未改密，仅设置页 / cookie 等无关写入）就原样保留明文行、指纹基稳定，不匹配（确为改密如 `set-password`）才丢弃旧明文写新 hash；`/api/auth/admin` 改为在 `_save` 之后用 `get_auth_plain_password()` 读「刚落盘的文件」来算指纹，与重启 reconcile 实际读到的材料逐字节一致（保留明文则 `"pw:"`、hash-only 则 `"ph:"`），彻底消除半状态/重启误撤销；⑨堵住 `config.local.toml` 覆盖层导致改密「假成功真回滚」：`load_config` 会把 `config.local.toml` 合并盖在 `config.toml` 之上（local 胜），若它钉了 `[api.auth].password` 等字段，admin / `set-password` 写 `config.toml` 会在重启时被悄悄盖回旧值且指纹掩盖了漂移。现在 `/api/auth/admin` 在 `_save` 后**重新加载有效合并配置校验改动确已生效**，被 config.local 遮蔽时回滚并返回 `409 shadowed`（而非假成功）；CLI `set-password` 在写盘前检测 `config.local.toml` 是否钉了 `password` / `password_hash` / `enabled` / `session_secret`（新增 `config_local_auth_keys()`），命中即拒绝并提示去改 config.local；⑩把 config.local 的 provenance 保护**下沉到 `save_config` 本身**（与 env 同源）：`_api_auth_lines` 此前只认 env 覆盖与盘上明文，仍会把 config.local 派生的 `[api.auth]` 值经 `PUT /api/config` / 启动 secret 生成 / init 等无关全量保存烤进 config.toml。现在新增 `_auth_overridden_fields()`（env ∪ config.local governed 字段），凡被任一覆盖层治理的字段一律渲染 config.toml 自身的盘上值、无值则省略（含 `trusted_proxies` / `allowed_bearer_origins`，它们无 env 覆盖但 config.local 可遮蔽），任何全量写都不再把覆盖层的值固化进基文件；⑪ config.local provenance 改为**路径感知**：`load_config(显式路径)` 根本不合并 config.local，故 `save_config(cfg, 其他路径)` 不再被项目根 config.local 误判遮蔽而吞掉显式路径的合法 auth 改动（`save_config` 仅在写默认路径时 `consult_local=True`）；并修复 admin 在 `config.toml` 原本不存在时改动被遮蔽的回滚——此前无备份只在有备份时还原，`409 shadowed` 会留下新建的 config.toml（含 `enabled` / `session_secret`），现在 `_rollback_cfg()` 在无备份且原文件不存在时删除新建文件，失败的遮蔽改动不留任何持久化痕迹；⑫ 终审独立审计补漏：CLI `--rotate-secret` 此前没调用 `set_password_fingerprint`（该方法 docstring 正是为它而写），导致轮换后首次重启 reconcile 会在已撤销之上再做一次冗余 epoch bump；现在 `_rebase_auth_fingerprint()` 在轮换后用新密钥重存指纹，重启 reconcile 不再多撤销一次（无害但消除困惑）。
- 修复候选池 `pool_target_count` 卡在 raw B 站库存而前端可换数到不了目标的问题：补池来源缺口改用 `count_pool_available_candidates_by_source()`，与 `count_pool_candidates()` 同口径应用预生成 / 分类 / 可打开 / 最近看过过滤和全局 topic window；B 站 raw=300 但 frontend available=246 时会继续请求 54 条，而不是误判已满。
- 候选池 cap 从“raw 等于 `pool_target_count`”拆成“前端可换目标 + raw material ceiling”：raw 库存可增长到 `max(pool_target_count * 2, pool_target_count + 120)`，请求侧按 raw headroom 夹住，cap 侧用 raw ceiling quotas 修剪，避免从 300 死锁挪到 600 churn。
- XHS pending 库存纳入 raw material 统计和 raw trim：未带 `xsec_token` 的小红书行会消耗 raw headroom，达到 raw 配额后 producer / reactivation 停止继续加货；raw trim 采用 least-servable-first，先丢不可打开 / 未就绪行，再按 relevance / recency 排序，避免保留 pending 行却删掉可打开候选。
- 测试：新增 storage 层 available-by-source parity、raw-material parity、pending XHS trim / reactivation 回归；refresh runtime 新增 available 缺口、raw headroom clamp、raw ceiling cap 和真实 SQLite 300 raw / 246 available → 300 available 的端到端回归。
- 修复插件 side panel 里收藏 / 稍后再看的当前会话同步问题：新增 `popup-saved-sync.js`，把推荐卡的稍后再看、惊喜横幅的稍后再看 / 收藏、收藏列表移除接到同一套 bvid 状态注册表，任一按钮写入成功后所有可见按钮会同步 `aria-pressed`、标题和文本状态。
- 修复旧懒加载状态覆盖新状态的竞态：按钮渲染后发出的 `GET /api/watch-later/{bvid}` / `GET /api/favorites/{bvid}` 如果在用户刚 toggle 或收藏列表加载 / 移除之后才返回，不再把 UI 状态回滚到旧值；并补充并发懒加载查询不会互相作废的回归测试。
- 修复状态注册表的按钮泄漏：推荐卡与惊喜横幅每次重渲染都会为同一 bvid 注册新按钮，旧的已脱离 DOM 的按钮此前不会被回收。现在 `syncButtons` 在每次状态同步时剪除 `isConnected === false` 的条目，并在推荐列表 / 惊喜横幅 `replaceChildren` 后调用 `pruneDetached()` 主动扫除，避免注册表随会话无限增长、`syncButtons` 退化为 O(累计渲染数)。
- 测试：新增 `extension/tests/popup-saved-sync.test.ts` 覆盖同 bvid 多按钮同步、用户点击后忽略旧状态、收藏列表外部状态写入后忽略旧状态、并发懒加载共享版本、游离按钮被剪除后不再更新；同步更新 popup 收藏 / 稍后再看静态布线断言。
- 修复「每条推荐理由都一样、且和视频对不上」：`_precompute_batch` 在 LLM 返回数组**不带 bvid/content_id** 时会退化成按数组下标硬塞文案，弱模型（如上下文被截断的 `qwen:7b`）一旦乱序 / 重复输出就把文案张冠李戴并静默写池。现在多条候选缺 ID 时直接回退逐条生成（单条调用各自携带 bvid，不会错位），单条批次仍走原位置匹配（无歧义）；并新增去重闸：同一句文案被分配给多个不同 bvid 时整组丢弃，宁可不发也不发重复文案。
- 修复本地 Ollama 上下文窗口被静默截断：新增 `[llm.ollama] num_ctx`（默认 `0` 保持原 `/v1` 行为）。Ollama 的 OpenAI 兼容 `/v1` 端点会丢弃 `num_ctx`，大批量 prompt 超 4096 即被截断、导致结构化 JSON 解析失败。设 `num_ctx > 0` 后聊天改走原生 `/api/chat` 端点（`OllamaProvider._complete_native`，`max_tokens→num_predict`、`json_mode→format=json`、空响应回退一次无约束重试），`options.num_ctx` 才真正生效（已实测 `context_length` 变为 8192）。
- 测试：新增 `_precompute_batch` 无 ID 多条回退逐条、重复文案整组丢弃回归；新增 `OllamaProvider` num_ctx 路由原生端点 / json_mode→format / 默认走 `/v1` shim / 空响应去约束重试单元测试。
- 修复「语义去重未启用」横幅**根本无法隐藏**的 CSS bug（自 v0.3.54 起一直存在,影响所有用户）：`.embedding-banner { display: flex }` 在同等优先级下盖过浏览器 UA 的 `[hidden] { display: none }`,导致 `banner.hidden = true` 形同虚设——无论 `embedding_ready` 真假、无论是否点关闭,横幅都常驻显示。新增 `.embedding-banner[hidden] { display: none }`（优先级 0,2,0 > 0,1,0）守卫,`hidden` 重新生效。**这正是「embedding 配置好了横幅还在」的真正可见症状。**
- 修复 `embedding_ready` 信号失真（横幅显示与否的依据）：`/api/health.embedding_ready` 从「服务是否构建」改为**实时探活**,既堵住「模型 404 全挂但仍报已就绪」的假阴性,也让修好后能恢复。新增 `EmbeddingService.probe()` 绕过 L1/L2 缓存直接打一次 provider（缓存命中的旧成功不会掩盖 provider 已掉线 / `bge-m3` 没拉），`/api/health` 侧带 `_EMBEDDING_READY_TTL_SECONDS`（默认 30s）+ single-flight,避免频繁 health 轮询打爆 provider；探活由 `_EMBEDDING_PROBE_TIMEOUT_SECONDS`（默认 6s）上限兜住绝不阻塞 health。**超时按「模型冷加载中」乐观判 ready 并缓存**——Ollama 闲置后会卸载 bge-m3,首次重载约 3s,真缺模型则快速 404 仍判 not-ready,这样既不会每次开面板都闪一下横幅,也不会让并发/重复 health 各自重探把延迟叠到 10s+。服务对象不存在仍报 `false`,无 `probe()` 的旧服务回退「构建即就绪」。
- 插件侧把横幅决策抽到 `popup-embedding-banner.js`（`shouldShowEmbeddingBanner`），并在 side panel 重新可见 / 获焦时复检（`installEmbeddingBannerAutoRefresh`）——此前 `maybeShowEmbeddingBanner` 只在面板打开时跑一次，常驻面板在 embedding 修好后仍长期残留旧横幅；现在配合后端实时探活，修好后无需重开面板横幅即自动消失。
- 测试：新增 `EmbeddingService.probe()` 成功 / 空向量 / 异常 / 绕过缓存逐次打 provider 回归；`/api/health` 探活成功→ready、探活失败→not-ready（模型没拉场景）、结果缓存共享一次 provider 往返；前端 `popup-embedding-banner.test.ts` 覆盖 show/hide 决策、可见 / 获焦复检、隐藏时不复检、teardown 摘监听,并加 `.embedding-banner[hidden]` display:none 守卫的结构化回归（防止 un-hideable 横幅再现）。

## 可编辑用户画像 · Phase 2/3：插件 + Web 编辑 UI（2026-05-29）

- **三端可编辑画像 UI**：插件 side panel、移动 Web（`/m`）、桌面 Web（`/web`）画像页都新增「编辑画像」开关，进入后是由未截断的 `GET /api/profile/edit-state` 驱动的编辑面板——chip 增删（核心特质 / 深层需求 / 价值观 / 内在驱动 / 认知风格 / 常看 UP）、兴趣树领域增删（喜欢 / 不喜欢）、长文改写（人格素描 / 人生阶段 / 当前阶段）。
- **确定性 + 可撤销**：每个控件 POST 一次 `/api/profile/edit`，从返回的 `edit_state` 即时重渲染；文本固定项显示「AI 想更新此项」漂移建议，任一改过的字段可「恢复 AI 建议」（reset）。编辑抗画像重建（后端覆盖层）。
- **覆盖三套前端**：实现时发现桌面 Web（`/web`，`web/desktop/`）与移动 SPA（`/m`，`web/`）是**两套独立前端**（Phase 1 设计文档曾误以为同一套），本期分别接入；插件为第三套。
- **补齐标量滑杆字段（修编辑面板缺口）**：三端编辑面板此前只渲染 chip / 兴趣 / 长文三类，漏掉了后端 `edit-state` 早已输出的 4 个标量字段（探索开放度 / 质量敏感度 / 幽默偏好 / 深度偏好）——用户进编辑模式后这些 section 既无控件也无「保存」按钮。现补 `renderScalarEditField`（百分比滑杆 + 显式「保存」，拖动实时回显、松手不自动提交，`op=set` 提交 0..1 浮点），并把 4 个 path 接入三端 `EDIT_FIELD_ORDER` / 标签表；面板提示同步澄清「chip / 兴趣增删即时生效，长文与滑杆点保存才生效」（原提示笼统说「即时生效」与长文/滑杆的显式保存矛盾）。
- **修「新增避雷方向像没保存」（编辑请求被清池阻塞）**：往「不喜欢」加领域时，`SoulEngine.apply_user_edit` 会在请求内**同步 `await`** 拉黑清池（`purge_pool_for_new_dislikes` 的 embedding 召回 + LLM 分类），实测让 `POST /api/profile/edit` 卡到 60s 超时——前端 `submitProfileEdit`（35s 超时）期间不刷新，用户看到的就是「加了避雷没反应 / 没保存」。likes / 列表 / 长文 0.02–0.14s 不受影响（不触发清池）。修复：覆盖层已先持久化，清池改为 `asyncio` **后台 detached 任务**（`_schedule_dislike_purge` + `wait_for_pending_edits`），编辑请求立即返回；实测 dislike 新增从 60s 超时降到 0.14s，端到端 chip 205ms 出现。
- **修「编辑态能看到、只读态看不到」（用户编辑被显示上限截断）**：`/api/profile-summary` 用有效画像（AI ⊕ 覆盖）但对各列表字段做硬截断（`core_traits[:6]`、`deep_needs[:5]`、`values[:5]`、`motivational_drivers[:4]`、`cognitive_style[:5]`、`likes[:12]`、`dislikes[:8]`、`favorite_up_users[:8]`）。覆盖层把用户新增项**追加在 AI 项之后**，于是任何排到上限之外的手动编辑在只读视图被切掉，却在编辑态（`edit-state` 不截断）可见——例如 AI 已有 6 条核心特质时，用户加的第 7 条「喜欢探索」只读态消失。新增 `_cap_keeping_user_added(items, added, limit, key)`：截断只作用于 AI 推断项，**用户手动新增项永远保留**（少量且有意），对全部 8 个截断字段生效。
- 测试：插件新增 `tests/popup-profile-edit.test.ts`（typecheck + 348 例全绿，含滑杆渲染断言）；新增后端 scalar set/reset round-trip 测试、`apply_user_edit` 清池不阻塞响应的回归测试、`_cap_keeping_user_added` 单元测试 + 「summary 保留超上限的用户新增项」over-the-wire 测试；三端 JS `node --check` 通过；后端编辑 API over-the-wire E2E 全绿。对应 issue #19。

## 可编辑用户画像 · Phase 1：后端覆盖层（2026-05-29）

- **新增 `soul/overrides.py` 覆盖层**：用户对画像的手动编辑写入独立 `data/memory/profile_overrides.json`，AI 画像照常存 `soul.json`。**有效画像 = AI 画像 ⊕ 用户覆盖**，在读收口 `SoulEngine.get_profile()` 与镜像收口 `MemoryManager.sync_profile_files()` 叠加——三条画像重建落点不变，用户编辑天然不被重建覆盖。
- **确定性字段级编辑**：`apply_edit` 归约器支持文本固定、标量固定、列表增删、兴趣树增删/权重固定，含校验与 add/remove 互斥；`apply_overrides` 纯函数确定性合并，列表 remove 持续抑制 AI 再次推断出的同项。
- **删/拉黑真实影响推荐**：用户加入 `interest.dislikes` 的项经 `get_effective_disliked_topics()`（base-then-overlay，remove 最后生效，不被 raw preference 反向打穿）驱动 proactive delight 硬过滤；新增拉黑还会复用 `purge_pool_for_new_dislikes` 清掉已入池命中内容（按编辑前后差集触发，重复添加不重复清池）。
- **新增 API**：`POST /api/profile/edit`（一次确定性编辑，非法输入 422，返回最新 edit-state）、`GET /api/profile/edit-state`（**未截断**全量可编辑字段 + 覆盖标注 + 文本/标量固定项的 AI 漂移建议，编辑 UI 数据源）；`GET /api/profile-summary` 新增 `overrides` 标注（展示态，保持截断、向后兼容）。
- **两套 speculator 同步**：手动 like add/remove 同步正向 `InterestSpeculator`，dislike add/remove 同步 `AvoidanceSpeculator`，避免画像与猜测系统打架；每次编辑记一条 `source=manual` cognition。
- 测试：新增 `tests/test_overrides.py` 等共约 30 例（合并 / 抗重建 / 校验 / 有效 dislikes / 清池差集 / speculator 同步 / API 全量与截断）；后端 1843 passed 全绿，改动文件 ruff + mypy 干净。
- 说明：本期仅后端；插件端与 PC/移动 Web 编辑 UI 为 Phase 2/3。设计与实现计划见 `docs/plans/2026-05-29-editable-profile-design.md` 与 `docs/plans/2026-05-29-editable-profile.md`。对应 issue #19。

## v0.3.95 / extension v0.3.54: embedding 默认值兜底 + 语义去重未启用提示（2026-05-29）

- 修复「embedding 服务静默禁用 → 刷到换皮重复视频」的根因。bvid 级去重一直 100% 生效（同一 bvid 不会重复推荐），但同一内容的不同 ID（跨平台镜像 / 转载 / 同名系列）只能靠 embedding 语义去重 catch，而 embedding 一旦悬空就只剩日志一行警告、用户无感知。
- **新增 `/api/health` 的 `embedding_ready` 字段**：插件 popup 在 embedding 未启用时显示一条可关闭的提示横幅，「一键启用本地 Ollama」按钮直接 PUT `/api/config` 热加载并复检 health，成功才收起横幅（`fetchHealth` + `maybeShowEmbeddingBanner`）。
- **`openbiliclaw init` 自动兜底**：`_interactive_embedding_setup(auto_if_ready=True)` 检测到本机 Ollama 已运行且装有 bge-m3 时直接启用本地 embedding、跳过菜单；显式 `setup-embedding` 仍保留完整菜单以便切换 provider。
- **修复一句话安装的死代码兜底**：`agent_bootstrap.py` 的 `auto_embedding_to_ollama` 此前声明后从未置 True（兜底等于失效），导致「主模型选 Claude/DeepSeek/OpenRouter（不能做 embedding）却没单独配 embedding」时 embedding 悬空。新增 `should_auto_wire_embedding()`：embedding 未配置、用户未显式 `--embedding-provider ""` 关闭、且非 Docker 时，自动写入 `provider=ollama, model=bge-m3` 并拉取模型。
- 测试：新增 `embedding_ready` health 两例 + `should_auto_wire_embedding` 四例，后端 test_api_app / test_agent_bootstrap 全绿，扩展 typecheck 通过。
- 文档：同步 `skills/search/SKILL.md`（从陈旧分支捞回 + 校准到当前实现）——纠正「并发搜索」实为顺序 + 0.5–1.0s 抖动延迟，补齐 v0.3.61+ storm-mode / cooldown 与 `v_voucher` 3× 内部重试说明，去掉文档里不存在的 `limit≤50` 钳制 / 长关键词截断 / `Retry-After` 解析等描述。

## v0.3.94 / extension v0.3.53: 推荐封面图加载白闪修复（三端）（2026-05-29）

- 修复推荐封面图在向下滚动 / 加载更多时「先白一下再出来」的问题（三端）：移动 Web 封面改为全部 eager 加载、滚动预热窗口扩到 16 张 / 2400px；桌面 Web 封面 `lazy→eager` 并在「加载更多」前预解码新封面（`warmCoverImages`）；插件 popup 续页前预解码封面（`preloadCoverImages`）、自动加载阈值 96px→600px。封面在卡片进入视口前完成下载+解码，渲染即出图，不再露白底。
- 测试：扩展 344 passed、移动/桌面 Web JS 的 python 套件 31 passed 全绿。

## v0.3.93 / extension v0.3.52: 独立收藏夹与稍后再看浏览页（2026-05-29）

- 新增独立「收藏夹」功能：`favorites` SQLite 表（`_ensure_favorites_table` 自动 migration）+ 5 个 DB 方法 + 4 个 API 端点（POST / DELETE / GET 单条 + GET 列表，列表带 `limit/offset` 422 校验）。收藏与稍后再看是两个互相独立的本地集合，一个视频可同时/分别/都不在其中。Pydantic 模型 `Favorite{AddIn,StateResponse,Item,ListResponse}`。
- 三端均补齐收藏入口 + 浏览页：移动 Web 底部导航新增「收藏」tab（`initFavoritesView`）、桌面 Web 侧边栏「我的收藏」页（`favoritesBtn/favoritesPage/favoritesCountBadge`）、插件 popup 新增「收藏」tab（`viewFavorites/favoritesList`/`loadFavorites`）。推荐卡与 delight 卡均加 ♡/♥ toggle（乐观 UI、失败回退、懒加载状态）。
- 补完稍后再看「浏览页」（此前只有 ☆ toggle）：移动 Web「稍后」tab、桌面 Web「稍后再看」页 + 数量徽章、`fetchWatchLater` 列表 helper。移动端 `views/saved.js` 与桌面 `renderSavedList` 让稍后再看 / 收藏复用同一套已存内容列表组件。
- 修复 `GET /api/watch-later` 缺少分页参数校验：`limit/offset` 改用 `Query(ge=...)`，非法值返回 422。
- 测试：新增 `tests/test_favorites_api.py`（CRUD / 分页 / 校验 / 与稍后再看互相独立）+ 6 项扩展前端测试（`web-favorites.test.ts` + popup-api favorites helper）。修复 `test_api_app.py` 中 `FakeDatabase.get_recommendations` mock 缺 `exclude_processed` 参数导致的 2 项历史失败。后端 1793 passed、扩展 344 passed 全绿。
- 文档：新增 `docs/specs/favorites.md`，更新 `docs/specs/watch-later.md`（浏览页已实现）。
- UI 打磨（端到端真实数据验收）：
  - 图标语义统一为 **收藏 = ⭐星星 / 稍后再看 = 🕐时钟**（一眼可辨），全部改用与「点赞/点踩」同款的 SVG 图标族（line-icon），不再用 ☆/♥ Unicode 字形与 SVG 混排。桌面端推荐卡 + 惊喜横幅的收藏/稍后**回到底部反馈行内**和喜欢/不喜欢正常并排展示（先前移到封面右上角的方案因不够美观已撤掉）；状态由 `aria-pressed` + CSS 驱动（星星选中填充金色 `#e8a33d`、时钟选中 accent 色），不再做字形替换。
  - 移动端推荐卡的收藏/稍后保留封面右上角玻璃态 chip（小屏更省空间），图标同步为时钟/星星 SVG；惊喜 tray 的两个保存键为紧凑 SVG 图标。底部 tab 图标：稍后=🕐、收藏=⭐。
  - 侧边栏「我的收藏 / 稍后再看」导航、移动端「收藏 / 稍后」列表页的头部与空态图标全部同步为星星 / 时钟；各处空态文案不再提 ☆/♥。
  - 收藏/稍后浏览页的「移除」由橙色实心按钮改为安静的 ghost 描边按钮。

## v0.3.92 / extension v0.3.51: OR-join 去重修复与稍后再看功能（2026-05-28）

- 文档：全面重绘 `soul`、`recommendation` 与 Web HTML 三个模块的 HTML 架构图 / 流程图，并在文档导航和架构说明中补齐可视化入口。
- 修复 `recommendations ↔ content_cache` 的 6 处 OR-join（`ON c.bvid = r.bvid OR c.content_id = r.bvid`）在多平台内容下产生重复行的问题，改用 COALESCE 子查询保证每条推荐最多匹配一条 content_cache 行。同时修复 curator 的 topic / UP / franchise fatigue 计算因重复行被放大的问题。
- `get_recommendations()` 新增 `exclude_processed` 参数，API 层传 `True` 排除已反馈推荐，activity_feed 等调用者保持原行为。
- 新增「稍后再看」本地书签功能：`watch_later` SQLite 表 + 4 个 API 端点（POST / DELETE / GET 单条 + GET 列表）。移动 Web、桌面 Web、插件 popup 的推荐卡和 delight 卡均增加 ☆/★ toggle 按钮，支持乐观 UI、失败回退和懒加载状态同步。
- 感谢 [@jiaobenhaimo](https://github.com/jiaobenhaimo)（[#53](https://github.com/whiteguo233/OpenBiliClaw/pull/53)）发现 OR-join 重复行问题并提出稍后再看功能设计。

## v0.3.91 / extension v0.3.50: XHS 自发布内容推荐池过滤（2026-05-27）

- 一句话安装的 `agent_bootstrap.py` 在自动运行 `openbiliclaw init` 前新增 LLM provider + embedding 服务真实轻量校验；任一失败会返回 `service_check_failed` 并阻止 init，提示用户修 API key / base_url / model / Ollama 后重跑，避免生成空画像或半残推荐池。
- 修复移动 Web 消息区避雷探针按钮竞态：点击「确实不喜欢」后会立即锁住同一卡片的其它动作，避免继续点「不是」形成 confirm + reject 双请求；后端只在 active 探针真实命中时写入 `probe_feedback_history` / `avoidance_probe_feedback_history`，stale 点击不再污染反馈历史。
- 修复移动 Web 消息收件箱空态关闭失效：空消息提示不再用 `panel.innerHTML +=` 重建整个面板，避免清掉 X 按钮的 click handler。
- 修复小红书登录用户自己发布的笔记被推荐回给自己的问题：`get_pool_candidates` / `count_pool_candidates` / `count_pool_readiness` 及后台整理查询（evaluation / copy / delight）在 SQL 层增加 self-author guard，排除 `up_name` 或 `author_name` 匹配自身昵称的小红书行；Bilibili 等其他平台不受影响，空昵称为安全 no-op。
- `_purge_self_authored_pool_items` 现在同时匹配 `up_name` 和 `author_name` 两列（此前只查 `up_name`），改昵称后旧行也能被清理。
- `_persist_xhs_self_info` 在 self_info 首次到达或内容变更时立即触发一次 purge，缩短"self_info 未到达"窗口期内自发布内容停留在池中的时间。
- `RecommendationEngine` 新增 `xhs_self_info_provider` 回调参数；`RuntimeContext`、`ContinuousRefreshController` 和 CLI 推荐引擎构造处均已接入，`Database` 保持纯存储层不直接读 runtime state。
- 新增 4 项 DB 层单元测试 + 2 项 API 层测试 + 4 项端到端生命周期测试（含大小写不敏感、幂等、昵称变更场景）。

## v0.3.91 / extension v0.3.49: 挑战式兴趣探针与跨源推荐点击修复（2026-05-25）

- 安全清理：移除误提交的 `config.toml.bak`，并将 `config.toml.*` 加入 ignore，避免本地配置备份文件再次进入版本库。
- 修复 YouTube 推荐点击的跨源链路：推荐卡片和移动 Web 现在向 `/api/recommendation-click` 同步上报 `content_id / content_url / source_platform`；后端会从 payload 或推荐记录补齐来源，YouTube 点击会写成 YouTube URL 和 `source_platform="youtube"` 的事件 / 强画像信号，不再把 `KPoJ7p9iy4Q` 这类 YouTube ID 记成 B 站 BV 号。惊喜推荐 payload 也暴露 `content_url / source_platform`，前端 URL fallback 会按来源构造。
- 主页 SEO 全面补齐：`docs/index.html` 增加 canonical、hreflang(zh-CN/en/x-default)、完整 OG + Twitter Card、JSON-LD（SoftwareApplication + SoftwareSourceCode + WebSite）、关键词、theme-color、preconnect；i18n 切换语言时同步覆盖 title / description / og / twitter / locale。首屏 hero 图 `fetchpriority=high`，截图全部 `loading=lazy`+显式宽高，CLS 0.00 / LCP 197ms。Lighthouse 移动 + 桌面四项均 100。新增 `docs/sitemap.xml`（含 image sitemap）、`docs/robots.txt`、`docs/seo.md`（Search Console / Bing 提交清单 + 长期维护要点）。
- 后端源码版本仍为 v0.3.91；浏览器插件版本提升到 extension v0.3.49，准备发布 `extension-v0.3.49`；v0.3.48 已发布，此次补发跨源推荐点击修复。
- 兴趣探针新增 near / lateral / bridge / wildcard 四档挑战距离，system prompt 保留距离定义，运行时按近期历史和画像状态控制探索远近。
- 探针反馈改成 4-way 语义：`positive`、`weak_positive`、`negative`、`neutral`；聊天、卡片、OpenClaw adapter 和 avoidance probe 的反向语义都走同一套写回分支。
- 弱正向兴趣探针先进入短期 exploration buffer，只有积累到足够显式信号后才晋升为正式兴趣，避免单次“有点意思”造成推荐短期刷屏。
- 推荐侧对新确认方向增加放大保护和 per-refresh 上限，新兴趣可以参与探索，但不会立刻挤占整批推荐。
- 修复配置热重载后只触发正向兴趣 speculator、漏掉避雷 speculator 的问题；热重载 one-shot 现在会同时调度 `post_reload_avoidance_speculate` 并传入 `avoidance_probe_feedback_history`。避雷 speculator 增加生成 / 转正 / 拒绝 / quality gate 日志，pipeline tick 异常会以 warning 暴露，避免 refresh loop 静默吞掉。
- 避雷探针新增 source/topic 级别去重：同一 `source_mode` 下的同一粗主题（如 AI 正向边界）只保留一条 active，重复 active 会在下一轮 tick 压入 cooldown；生成 prompt 也会携带 `existing_avoidance_details` 并要求避开同源换皮候选，避免一屏都是 AI 教程 / 测评 / 趋势类避雷。
- 挑战式兴趣探针改为独立 active 额度：普通 `near` 探针继续最多 5 条，`lateral/bridge/wildcard` 合并为挑战池并单独最多 3 条；5 个普通探针占满时，热重载 / force tick 仍能生成挑战探针，生成 prompt 也会切到 challenge-only 补货提示。
- 插件 side panel、移动 Web 和桌面 Web 的消息区把普通 `near` 兴趣探针、`lateral/bridge/wildcard` 挑战探针和避雷探针分成不同视觉语义与提示文案：普通兴趣用于“继续探索”，挑战探针提示“把口味往侧边推一点”，避雷用于“少看这类 / 猜错点不是”。
- 移动 Web 推荐页首屏请求增加超时兜底：推荐 / 惊喜推荐最多等待 12 秒，runtime status / activity 最多等待 5 秒；推荐接口慢或暂时失败时会结束 loading 并显示当前可用状态，避免手机端一直停在加载中。
- 移动 Web 推荐页加载优化：`recommendations.created_at/id` 与 `content_cache.content_id` 增加读取索引，修复 `/api/recommendations` 的双表扫描；推荐页首屏先渲染 `/api/recommendations` 结果，再异步补 runtime status / activity / delight，消息 badge 首次加载不再额外拉取未使用的 delight batch。
- 插件 side panel 与移动 Web 不再把后台 `refresh.pool_updated` 当成推荐列表全量重拉信号；该事件现在只同步池子状态 / header，用户向下滚动 append 出来的历史卡片不会被 `/api/recommendations` 最新前 20 条覆盖，只有主动“换一批”、初始化或重连类全量 hydration 才替换列表。

## v0.3.91 / extension v0.3.47: 真实可换库存口径修正 + 不喜欢领域探针（2026-05-24）

- 后端源码版本提升到 v0.3.91，准备发布 `backend-v0.3.91`；浏览器插件版本提升到 extension v0.3.47，准备发布 `extension-v0.3.47`。
- 修复 runtime status / runtime stream 的候选池数字口径：`pool_available_count` 现在只表示后端当前可立即 `serve()` 的候选；新增 `pool_raw_count` / `pool_pending_count` 用于区分素材库存和待整理内容，避免“池子有素材”被显示成“还有 N 条可换”。
- `count_pool_candidates()` 读取前会刷新 SQLite/WAL snapshot，避免同一次操作里 runtime status 看到旧库存、`get_pool_candidates()` 看到新状态而返回空。
- `count_pool_candidates()` 现在默认应用与 `get_pool_candidates()` 相同的 `max_per_topic_group=3` 候选窗口；单个 `topic_group` 堆积大量内容时，UI “可换”数量不再高于 `serve()` 实际可加载库存。
- 推荐 serve 的零候选 warning 增加 `raw/servable/pending` 诊断字段，方便区分 Gemini quota / 分类文案未完成导致的 pending，和真实 count/load 查询漂移。
- 插件 side panel、移动 Web 和桌面 Web 统一显示真实可换数；当 `pool_available_count=0` 且 `pool_pending_count>0` 时显示“找到 N 条素材，正在整理成可换内容”，不会把 pending 数量写成“可换”。插件手动“换一批”空结果会重新同步 runtime status，并用单飞锁避免重复点击竞态。
- 新增不喜欢领域探针设计与实现：系统会主动确认可能的避雷方向，移动 Web / 桌面 Web / 浏览器插件 / OpenClaw 都可查看和操作。
- 确认后通过 `apply_new_dislikes()` 写入 `disliked_topics` 并触发候选池清理；未确认避雷方向不参与 discovery / recommendation 过滤。
- 避雷探针聊天使用 durable `scope=avoidance_probe`，用户在多聊中确认或否认会走同一条反馈、写回与冷却路径。

## v0.3.89 / extension v0.3.44: 惊喜推荐内联多轮聊天（2026-05-22）

- 修复用户显式配置 `[llm.embedding].provider = "openrouter"` 仍然报 `No embedding-capable provider available (requested='openrouter')` 并禁用 embedding 的 bug：`_EMBEDDING_CAPABLE_PROVIDERS` 漏了 `openrouter`，dedicated 构建分支也没有 OpenRouter 路径。现在 registry 显式支持 OpenRouter embedding（必须配 `model = "<vendor>/<model>"`，例如 `google/gemini-embedding-2-preview`；无显式 model 时拒绝构建，避免运行时 404），`[llm.openrouter]` 的 `http_referer` / `x_title` 也会透传到 embedding 实例。`OpenRouterProvider.supports_embedding` 仍保持 `False` —— 只有用户显式选 openrouter 才走这条 dedicated 路径，不污染 chat-side 的自动回退链。
- 修复桌面 Web 推荐卡片点击「忽略」时 `/api/feedback` 返回 422 的回归：`feedback_type` 白名单新增 `dismiss`（CLI / API / OpenClaw adapter 同步放行）。dismiss 走「软移除」语义——`content_cache.pool_status` 标记为 `feedbacked` 让候选不再被重新发现，前端按 `feedback_type` 非空过滤掉已忽略卡片；soul 与 preference 分析忽略 dismiss 事件，不会把单次软忽略升成话题级负反馈。`activity_feed._feedback_items` 现会显示「这条你忽略了：{title}」而不是落到 fallback 的「写了一句反馈」。
- 浏览器插件版本提升到 extension v0.3.44，准备发布 `extension-v0.3.44`；后端源码版本仍为 v0.3.89，不发布新的后端 tag。
- 移动 Web 惊喜推荐的「聊一聊」不再切到对话 tab，而是在当前惊喜卡片内展开 16px textarea composer，提交后就地显示用户气泡、AI thinking、完成回复或失败提示。
- 移动 Web 和插件的惊喜推荐内聊统一走 durable `/api/chat/turns`，按 `scope=delight` + `subject_id` 归并历史；pending turn 会轮询恢复，reload 后可重新 hydrate。
- `[llm].concurrency` 新增为全局 LLM 请求并发上限，默认从 1 提升到 3，并接入 `/api/config` 与插件设置页「模型」tab，方便在速度和上游限流之间调整。
- 插件、桌面 Web 与移动 Web 的 runtime-stream 自动刷新新增 debounce / single-flight：后台补货事件密集时会合并 activity、recommendation、profile 等刷新请求，避免 LLM 并发提升后前端重复拉取和渲染造成卡顿。
- 后端独立候选池文案预计算完成后会回写 `last_replenished_count` 并广播 `refresh.pool_updated`，修复候选已进入可换库存但前端仍显示“这轮没补进”的状态错位。
- 推荐候选池 serve / 计数 / 文案预生成入口统一加 `style_key` 与 `topic_group` 非空门控；未分类内容必须先经过 `classify_pool_backlog`，不会再先生成推荐文案后绕过分类口径进入换一批。
- API runtime 与 OpenClaw direct bootstrap 读取 `[llm].concurrency` 时统一使用默认值兜底；旧测试夹具或精简配置缺少该字段时不再在组件构建阶段抛 `AttributeError`。
- embedding 预热从 refresh 收尾主路径改为后台 task；慢本地 embedding 后端只影响后续 MMR cache / topic supergroup cache 命中率，不再让 `manual_refresh_state` 长时间停在 `running` 或占住 refresh lock。
- `[scheduler].pool_target_count` 默认从 600 降到 300；B 站初始化关注默认从 300 收敛到 100，减少长关注列表对首次画像的事件量。XHS / Douyin / YouTube `bootstrap_profile` 的 `max_items_per_scope` 仍默认 300。
- 移动 Web 与插件 / side panel 推荐列表的自动续页新增用户滚动意图门闩；后台 `refresh.pool_updated` 或列表重渲染不会在加载更多哨兵仍可见时连续调用 `append`，避免候选刚补进就被空转消费到 0。
- B 站 search 连续命中 `v_voucher` / `412` 后会进入进程级冷却（10 分钟起，连续风控逐步延长到 30 分钟）；Search / Explore / RelatedChain 的搜索路径在冷却期直接跳过 query/domain 生成，避免每 60 秒继续撞风控并浪费 LLM token。
- Discovery 批量 LLM 评估前会跳过最近看过的内容，判断从单一 BVID 扩展为 `source_platform:content_id`；B 站保留 raw BVID 兼容，小红书 / 抖音 / YouTube 等来源也会在 LLM 前、写入候选池前和 pool 读取时被过滤，减少重复发现带来的 token 浪费。
- 移动 Web 推荐列表新增封面预热和接近底部自动续页：首屏推荐封面用 eager/high priority 加载，后续封面通过 `/api/image-proxy` URL best-effort 预热；滚到列表底部附近会自动调用 `append` 续下一批，底部「加载更多」按钮保留为兜底。
- 移动 Web 推荐列表的高速滑动封面体验继续收敛：当前批次默认预热 12 张封面，前 12 张用 eager 加载，追加批次会先等待封面预热/解码或短超时再插入卡片；封面图加载和 decode 完成前保持透明，让粉蓝渐变骨架先显示，decode 完成后淡入，减少快速下滑时的白屏闪烁。
- 插件惊喜推荐卡片从单个 `chat_reply` 升级为 per-delight `turns` 多轮气泡，`chat_reply` 仅保留为兼容 last reply；切换候选和 side panel reload 不再覆盖旧回合。
- 修复兴趣探针聊天反馈的情绪判断：`/api/interest-probes/respond` 的 sentiment LLM 调用改为普通文本模式，不再把只需 `positive / negative / neutral` 的标量分类请求发送成 `json_object`，避免 DeepSeek 返回 400 后频繁落到关键词 fallback。
- 修复兴趣探针 WebSocket 投递语义：`interest.probe` 只有实际投递到至少一个 `runtime-stream` 订阅者后，才写入 `probed_domains` / `probed_axes` 冷却状态；前端离线时不会把探针误标为已问过。
- Discovery / recommendation 的批量内容评估统一透传近期 negative exemplars：B 站、抖音、YouTube 策略和 OpenClaw bootstrap 都会把共享 database 传给内部 evaluator；推荐层的未分类池子补评估也会带上 `negative_examples`，让短期话术避让与长期 `disliked_topics` 一起生效。
- 补充移动端回归测试，锁定 delight inline chat 复用 `session=popup` 契约、`chatted` 状态继续保留「聊一聊」入口，同时 viewed/liked/rejected 等永久处理态不泄漏通用动作按钮。

## v0.3.89 / extension v0.3.43: 显式 fallback 与限流降噪发布（2026-05-22）

- 后端源码版本提升到 v0.3.89，准备发布 `backend-v0.3.89`；浏览器插件版本提升到 extension v0.3.43，准备发布 `extension-v0.3.43`。
- LLM provider 限流 / cooldown 时，discovery eval batch 和 recommendation copy batch 不再退回逐条 LLM 调用，避免一次 Gemini 429 放大成整批 traceback；XHS / 抖音 / YouTube task claim 改用短生命周期 SQLite 连接，修复并发 `/next-task` poll 的嵌套事务错误；`httpx` / `httpcore` 文件日志默认降到 WARNING。
- 插件设置页将 LLM / embedding fallback 从“自动尝试其它 provider”改成显式“备选 Provider”下拉框；`fallback_provider = ""` 时完全不 fallback，非空时只尝试这一个备选 provider。
- `/api/image-proxy` 不再把 redirect 白名单失败、非图片 Content-Type、超过 10MB 和超时统一折叠成 502；校验类错误保留 403 / 400 / 413，网络超时返回 504，缓存回退只用于上游网络失败或 5xx 类错误。

## v0.3.88 / extension v0.3.42: 局域网二维码与封面代理合并发布（2026-05-21）

- 浏览器插件版本提升到 extension v0.3.42，合入 extension v0.3.41 的封面代理发布内容，并补齐 main 上的移动端二维码局域网 IP 自动检测逻辑；当插件后端仍配置为 `127.0.0.1` / `localhost` 时，会读取 `/api/health.lan_ip` 生成手机可访问的 `/m/` 二维码。
- 一句话安装和 agent bootstrap 默认绑定 `0.0.0.0:8420`，健康检查仍使用 `127.0.0.1` URL；`/api/health.lan_ip` 优先返回 RFC1918 网卡地址并排除 `198.18.0.0/15` VPN / TUN 地址，避免二维码显示手机不可达 IP。
- `openbiliclaw init` 的 B 站收藏和关注初始化信号默认各限制为 300 条 / 人，并新增 `--bilibili-favorite-limit` / `--bilibili-follow-limit` 覆盖项；人类安装流程的 `agent_bootstrap.py --interactive-confirm` 会让用户确认这两个上限后再自动 init，避免大收藏夹和长关注列表把初始画像事件量拉得过高；B 站观看历史仍保持 300 条。

---

## v0.3.88 / extension v0.3.41: 插件封面代理发布（2026-05-21）

- 浏览器插件版本提升到 extension v0.3.41，推荐、惊喜推荐和消息封面统一走配置的本地后端 `/api/image-proxy`，不再直接暴露第三方 CDN 图片请求；本次仅发布插件包，后端源码版本仍为 v0.3.88。

---

## v0.3.88 / extension v0.3.40: 移动端视觉优化与局域网默认可达（2026-05-21）

- 移动 Web 惊喜推荐卡片视觉优化：封面图加 `shape-outside` 圆角环绕让文字沿圆角自然流动；推荐理由字号从 12px 提升到 12.5px、行高从 1.48 提到 1.68 并增加字距提升阅读舒适度；「推荐原因」标签改为品牌粉蓝渐变底 + 细描边；卡片圆角从 14px 加大到 18px 并增加右上角径向渐变光晕与多层阴影增强纵深感；小屏移除理由文本截断改为字号微缩。
- 移动 Web 推荐页 header 和推荐卡片视觉优化：For You 标签改为品牌渐变胶囊 + 阴影；标题字号 15→17px；换一批按钮加圆角描边；活动行加独立边框；pool chip 改为圆角方块；推荐卡片标题加粗至 15px、card-source 改为胶囊形态、表达文字行高提升、卡片加内发光和分层阴影。
- 新增 `[api]` 配置节：`host`（默认 `0.0.0.0`）和 `port`（默认 `8420`），`openbiliclaw start` 读取配置决定监听地址，不再硬编码 `127.0.0.1`。手机扫码即可直接访问移动端 Web。
- `openbiliclaw init` 新增网络绑定确认：交互式引导中会询问用户是否允许局域网设备访问（默认 Y），选择结果持久化到 `config.toml [api].host`。
- 健康检查端点 `/api/health` 新增 `lan_ip` 字段：通过 UDP connect trick 检测本机局域网 IP 并返回。
- 浏览器插件移动端二维码自动检测局域网 IP：当插件配置的后端地址是 127.0.0.1 时，自动从 `/api/health` 获取 `lan_ip` 并用局域网 IP 生成二维码，手机扫码直接可用。
- 修复 `[api]` 配置 round-trip：`load_config()` 现在会读取 `[api].host` / `[api].port`，`save_config()` 会写回 `[api]`；一句话安装脚本和 `agent_bootstrap.py` 默认绑定 `0.0.0.0`，健康检查仍使用 `127.0.0.1` URL，避免把 `0.0.0.0` 当作浏览器访问地址。
- 修复局域网 IP 检测优先级：`/api/health.lan_ip` 现在优先选择网卡上的 RFC1918 地址（如 `192.168.x.x`），并排除 VPN / TUN 常见的 `198.18.0.0/15` benchmark 地址，避免二维码显示手机不可达的虚拟网卡 IP。

---

## v0.3.88 / extension v0.3.39: 移动端 Web 主入口与 fallback 默认关闭（2026-05-21）

- 新增 `/api/image-proxy` 后端图片代理，移动 Web 和浏览器插件的推荐、惊喜推荐、消息封面统一经本地后端加载；代理限制白名单 CDN、逐跳校验 redirect、校验 `image/*` 类型和 10MB 实际字节，前端加载失败时保留固定比例占位。
- `[llm].fallback_enabled` 新增为默认关闭的 LLM 请求 fallback 开关；关闭时 `LLMRegistry.complete()` 只调用默认 provider，失败直接暴露。
- `[llm.embedding].fallback_enabled` 新增为默认关闭的 embedding fallback 开关；关闭时不切 provider、不借用 `[llm.<provider>]` 凭据，且 embedding provider 留空表示不启用，不再跟随默认 LLM。
- 浏览器插件设置页「模型」tab 增加 LLM fallback 与 embedding fallback 两个开关，并更新文案说明 embedding 与 LLM 独立配置。
- 移动 Web 新增轻量 view-model 适配层，推荐页池状态会读取 `/api/runtime-status` 的 `pool_available_count` / `last_replenished_count` / `recent_pool_topics`，画像页 MBTI 可渲染后端返回的 `{EI: {pole, strength}}` 对象形态；对话页兼容 `/api/chat/turns` 返回的 `reply` 字段，不再因字段形态不一致空白或漏显回复。
- 移动 Web 资源噪声收敛：根路径 `/favicon.ico` 现在复用 PWA 图标返回 PNG；推荐页封面会过滤直接 403 的小红书 CDN URL、把 B 站 `http` / protocol-relative 封面升到 HTTPS，并用 `no-referrer` 加载外链图片，避免浏览器控制台残留 favicon / hotlink 错误。
- 移动 Web 推荐页的惊喜推荐动作对齐浏览器插件：底部按钮改为「看看 / 喜欢 / 不感兴趣 / 聊一聊」，「稍后看」收进右上角关闭控件，并把「喜欢」写入 `/api/delight/respond` 的 `like` 反馈。
- 移动 Web 推荐页头部对齐插件：新增 `For You / 这几条，你大概会点开` 紧凑 header，把「换一批」放回首屏主操作位，池状态三枚 chip 改为「当前可换 / 最近补进 / 现在在忙」，活动状态降级为 header 内辅助行，「加载更多」移动到推荐列表底部。
- 移动 Web 推荐页头部再次压缩移动端状态区：三枚池状态从大卡片改成横向轻量 pill，活动摘要改成单行；`xhs-extension-*`、`dy-plugin-*`、`yt-*` 等内部来源名会在移动端显示为用户可读的中文短标签。README 移动端预览说明同步使用「不感兴趣」文案。
- 移动 Web 惊喜推荐改为接近插件的 compact banner：封面从全宽大图收敛为左侧小缩略图，右侧展示标签、标题、理由和来源，翻页控件并入标签行，减少首屏占用并保留「看看 / 喜欢 / 不感兴趣 / 聊一聊」动作。
- 移动 Web 惊喜推荐 compact banner 恢复独立推荐原因描述：`delight_hook` 作为短标签展示，`delight_reason` 带「推荐原因」标记并围绕左侧头图排版，右上角保留「稍后看」关闭入口，避免只剩标题和 hook 看不到推荐理由，同时让这张卡明显区别于普通推荐卡。
- README / README_EN 的移动端预览截图已刷新为当前 `/m/` 推荐页实际渲染图，展示惊喜推荐 compact banner、推荐原因环绕头图和插件一致的动作区。
- 移动 Web 画像页补齐与插件一致的画像细节：MBTI 显示可信度，使用场景显示“模式”，内容口味把 `long/slow` 等 raw 值本地化为中文标签，认知更新卡片保留后端 `context_line` 与 `source_label`。
- 移动 Web 对话页对齐插件主聊天会话：读取和提交都使用 `session=popup&scope=chat`，聊天回复完成后会刷新画像和活动流；消息 overlay 内的兴趣探测动作改为「喜欢 / 不喜欢 / 多聊聊」，惊喜推荐动作补齐「喜欢」，聊天输入框固定在底部并以两行高度起步，保留更多历史上下文可视空间。
- 新增移动 Web 原生重设计 spec，明确 `/m/` 与浏览器插件在推荐、画像、对话、消息和 delight 工作流上的功能对齐范围，以及手机端独立信息架构。
- 插件顶部功能区新增移动端二维码入口：点击手机图标会按当前插件后端地址生成 `/m/` 本地二维码，手机可直接扫码打开移动端 Web；若仍是 `127.0.0.1` / `localhost` 会提示先切到电脑局域网 IP。README 同步补充移动端推荐 / 画像 / 对话截图和扫码使用方式。
- 后端源码版本记录为 v0.3.88，并通过 `backend-v0.3.88` source tag 标记；不发布 backend GitHub Release / 桌面包，远端 `backend-v*` workflow 改为只校验 tag 与 `pyproject.toml` 版本一致。浏览器插件版本提升到 extension v0.3.39，准备发布 `extension-v0.3.39`。

---

## v0.3.87 / extension v0.3.38: runtime 配置真实生效（2026-05-20）

- Runtime: YouTube steady-state discovery now runs through an independent backend producer loop with per-strategy daily execution budgets, `min_interval_minutes` throttling, and source-deficit gating.
- `AccountSyncService` 现在会持久化同秒历史 bvid 集合、收藏 bvid 集合和关注 mid 集合；B 站账号同步只把新增历史 / 收藏 / 关注送进画像分析，避免消息推荐期间重复重放旧账号信号并浪费 LLM tokens。
- XHS / 抖音 / YouTube bootstrap task-result 新增跨任务 seen-key 过滤：任务表仍保留完整 partial / final 原始结果，但进入 memory / 增量画像前会跳过 `source_bootstrap_state.json` 里已见的 note / video / item key；抖音和 YouTube 队列也补齐 `in_progress` claim 与 6 小时近期任务复用，避免反复打开前台 tab 全量扫描。
- `[scheduler]` 新增真实 runtime 调度参数：refresh 轮询、行为触发阈值、trending / explore 间隔、单轮 discovery 上限、主动推送间隔和 speculator idle tick；这些字段已接入 `/api/config`、daemon runtime、OpenClaw direct bootstrap 和插件设置页。
- `scheduler.speculation_*` 现在会传入 `SoulEngine` / `InterestSpeculator`，配置页里的猜测兴趣间隔、TTL、冷却、确认阈值和上限不再只是保存到 TOML。
- 插件设置页调度区移除无效的 `discovery_cron` 输入，补上 `extension_disconnect_grace_seconds` 和实际生效的 runtime 频率控件；`discovery_cron` 仍作为 legacy 字段保留在配置/API 中但 runtime 不消费。
- README 快速开始保留插件安装、AI 部署后端和平台登录三步展开；后端其他部署路径继续折叠展示。
- 后端源码版本记录为 v0.3.87，但不发布 backend GitHub Release；浏览器插件版本提升到 v0.3.38，准备发布 `extension-v0.3.38`。

---

## v0.3.86 / extension v0.3.37: 小红书默认改为显式开启（2026-05-20）

- `[sources.xiaohongshu].enabled` 默认改为 `false`；小红书 discovery / init bootstrap 现在必须由用户在初始化时选择 Yes、传 `--yes-xhs`，或在插件设置页打开后才会启用。
- `openbiliclaw init` 的小红书交互提示默认从 Yes 改为 No；非交互环境也不再静默启用小红书 bootstrap，避免未安装扩展或未登录时自动排队任务。
- runtime 候选池默认有效配比改为只包含 Bilibili；`[scheduler.pool_source_shares]` 仍保存 Bilibili / 小红书 / 抖音 / YouTube = `8 / 1 / 1 / 1`，显式启用可选平台后才参与 quota。
- 插件设置页读取缺省配置时不再默认勾选「启用小红书 discovery」，保存和配比建议都以用户当前开关为准。
- 后端源码版本记录为 v0.3.86，但不发布 backend GitHub Release；浏览器插件版本提升到 v0.3.37，准备发布 `extension-v0.3.37`。

---

## v0.3.85 / extension v0.3.36: 插件配置页来源与日志整理（2026-05-20）

- `[sources.bilibili].enabled` 新增 Bilibili discovery 开关；关闭后 B 站 search / related_chain / trending / explore 不再参与后台补池，`pool_source_shares.bilibili` 会保留但从运行时有效配比中剔除。
- 插件设置页「平台源」tab 按 Bilibili / 小红书 / 抖音 / YouTube / 通用网页 / 候选池配比拆成独立分块，并把 B 站登录调试项文案改成「调试：B 站登录时显示浏览器窗口」。
- `/api/config` 的 logging 响应新增只读 `file_path`，返回由 `directory` + `filename` 解析后的完整日志文件路径。
- 浏览器插件设置页「日志」tab 将原来的「日志目录」+「日志文件名」收敛为单个「完整日志路径」输入；保存时仍拆回 `logging.directory` / `logging.filename` 写入 `config.toml`，兼容现有后端配置结构。
- 后端包版本提升到 v0.3.85，准备发布 `backend-v0.3.85`；浏览器插件版本提升到 v0.3.36，准备发布 `extension-v0.3.36`。

---

## extension v0.3.35: 插件聊天页贴底布局修复（2026-05-20）

- 浏览器插件聊天 tab 激活时会隐藏底部活动栏，让聊天输入框成为 side panel 底部固定区域；聊天记录区改为独立 flex 滚动，优先占用输入框上方空间。
- 压缩聊天消息、状态提示和输入区间距，空状态提示不再占位；textarea 保留两行起步并限制最大高度，长内容在输入框内部滚动。
- 浏览器插件版本提升到 v0.3.35，准备发布 `extension-v0.3.35`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.35.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.35-firefox.zip`。本次不发布后端包。

---

## v0.3.84: 安装渠道自动 init 收敛（2026-05-20）

- `agent_bootstrap.py` 新增交互确认模式和扩展 Cookie 等待流程：Bash / PowerShell / Docker / AI agent 安装渠道会在确认 embedding、B 站 Cookie 来源和小红书 / 抖音 / YouTube opt-in 后自动运行 init，不再把手动 `openbiliclaw init` 作为主路径。
- Docker bootstrap 会把宿主机确认后的 `config.toml` 与 Cookie 文件同步到容器 `/app/runtime`，并用容器 runtime config 判断是否具备 init 条件；`docker exec ... openbiliclaw init` 保留为高级手动 fallback。
- 后端包版本提升到 v0.3.84，准备发布 `backend-v0.3.84`。

---

## v0.3.83: 插件设置页分组与 YouTube 配置补齐（2026-05-19）

- 浏览器插件设置页按「模型 / 平台源 / 调度 / 通用 / 日志」分 tab，候选池来源占比移入平台源区，避免所有配置挤在同一个长列表里。
- `[sources.youtube]` 补齐 `daily_search_budget` / `daily_trending_budget` / `daily_channel_budget` / `request_interval_seconds`，并通过 `/api/config` 与插件设置页 round-trip；runtime 会把前三个预算传给 `yt_search` / `yt_trending` / `yt_channel` 对应策略。
- 后端包版本提升到 v0.3.83，准备发布 `backend-v0.3.83`；浏览器插件版本提升到 v0.3.34，准备发布 `extension-v0.3.34`。

---

## v0.3.82: 一句话安装合约对齐（2026-05-19）

- 一句话安装合约补齐 YouTube opt-in：`agent_bootstrap.py` 现在像小红书 / 抖音一样要求 `--yes-youtube` / `--no-youtube`，并把该选择传给自动 `openbiliclaw init`；`install.sh` / `install.ps1` 状态块和 agent/Docker/CLI 文档同步打印 YouTube 决策，同时统一 LLM 默认推荐为 DeepSeek 并修正安装文档的模型菜单编号。
- 后端包版本提升到 v0.3.82，准备发布 `backend-v0.3.82`。

---

## v0.3.81: 推荐理由错位修复（2026-05-19）

- 批量推荐文案、discovery batch 评估和源无关内容分类现在都携带并按 `bvid/content_id` 绑定 LLM 结果；provider 乱序、漏项或返回部分数组时不再把推荐理由 / 评估理由写到错误视频。
- 后端包版本提升到 v0.3.81，准备发布 `backend-v0.3.81`。

---

## v0.3.80: Docker 部署体验补强（2026-05-19）

- 后台 `AccountSyncService` 首次同步账号行为并完成 preference 分析后，如果 soul 画像层为空（典型场景：Docker 部署未跑 init），会自动触发 `build_initial_profile([])` 生成初始画像；每进程生命周期最多尝试一次，失败不影响后续同步。
- `/api/health` 新增可选 `profile_ready` 字段，返回 soul 画像是否已生成；字段缺失时保持旧响应兼容，不影响 HTTP 状态码和 Docker healthcheck 判定。
- Docker 部署文档和 README 补充 init 步骤提示，并新增「后端启动但无推荐」排查说明。
- 浏览器插件 Chat 入口文案拓宽为“想法 / 口味 / 自我描述 / 近期状态”方向，保留已有 placeholder 轮播机制，不再只暗示用户聊最近爱看的内容。
- 浏览器插件版本提升到 v0.3.33，准备发布 `extension-v0.3.33`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.33.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.33-firefox.zip`。
- 后端包版本提升到 v0.3.80，准备发布 `backend-v0.3.80`。

---

## v0.3.79: Popup 聊天输入体验补强（2026-05-19）

- 浏览器插件聊天 tab 新增多场景 placeholder 轮播，覆盖纪录片、测评、健身、怀旧动画、注意力、自我描述和近期状态等入口；输入框 focus 时暂停轮播，blur 且内容为空时恢复，避免用户正在输入时被提示语打断。
- 聊天历史区域高度从固定 `220px` 改为 `clamp(220px, 45vh, 420px)`：小窗口保持原有保底高度，侧栏拉高时可展示更多长回复，最高限制在 420px，避免挤压输入区。
- 偏好分析新增 prompt 预算保护：初始化 / bootstrap / feedback batch 不再只按事件条数分片，超长 chunk 会在本地继续拆分，单条超长事件会保守 compact，provider 返回 `n_keep >= n_ctx` 等 context-window 错误时会用更小 chunk 重试，避免一个巨大事件批次中断整轮画像初始化。
- 浏览器插件版本提升到 v0.3.32，准备发布 `extension-v0.3.32`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.32.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.32-firefox.zip`。

---

## v0.3.78: Codex OAuth 实验认证（2026-05-19）

- 新增实验性 `[llm.openai].auth_mode = "codex_oauth"`：OpenAI provider 仍复用现有 `OpenAIProvider`，但 token 来源改为本机 Codex CLI 的 ChatGPT OAuth 凭据；`codex_auth.py` 负责导入 `~/.codex/auth.json`、写入 `~/.openbiliclaw/codex_auth.json`、临期刷新和 401 后强制刷新重试。
- 新增 `openbiliclaw login codex`：支持默认导入 / 调用官方 `codex login` 后导入、`--import`、`--source`、`--status`、`--logout`；状态输出只展示账号和过期时间，不泄露 token。
- 配置和本地 API 增加 `auth_mode` round-trip；`codex_oauth` 下 `api_key` 会被忽略，且 `base_url` 只允许留空或指向 OpenAI 官方 API 域名，避免把 ChatGPT OAuth token 发给第三方 OpenAI-compatible 代理。
- 浏览器插件设置页同步支持 OpenAI `API Key` / `Codex OAuth` 认证方式选择，保存配置时会写入 `[llm.openai].auth_mode`；插件版本提升到 v0.3.31，准备发布 `extension-v0.3.31`。
- 明确风险边界：该功能是非官方实验集成，OpenAI 官方 API 认证稳定入口仍是 Platform API key，Codex CLI token 格式、权限和刷新行为可能随上游变化失效。

---

## v0.3.77: 浏览器插件局域网后端地址配置（2026-05-18）

- 浏览器插件设置页的后端 endpoint 从“仅端口可改”扩展为“后端地址 + 端口”一起配置：Chrome / Firefox manifest 都加入 `http://*/*` 权限，用户可把后端运行在局域网另一台机器上（`openbiliclaw start --host 0.0.0.0 --port 8420`），再在插件设置页填写该机器的局域网 IP；新增 host 校验、endpoint 持久化和 manifest 权限回归测试。
- 插件推荐页移除「停止后台 LLM 请求」和「关闭浏览器后停止后台」快捷开关，只在设置页调度区保留；弃用“省钱模式”旧称，并补充说明开启后不会自动补货，候选池为空时可能暂时没有推荐。`config-show` 同步显示「停止后台 LLM 请求」。
- 修复 [#27](https://github.com/whiteguo233/OpenBiliClaw/issues/27)：LM Studio 在 `json_object` / `json_schema` response format 下可能返回 HTTP 200 且后台 UI 可见模型输出，但 OpenAI-compatible API 的 `message.content` 为空；`OpenAIProvider` 现在识别本地 LM Studio 后从第一次结构化请求起不发送 `response_format`，依赖 prompt 约束 JSON，避免先浪费一整次 LLM 调用再重试。
- 浏览器插件版本提升到 v0.3.30，准备发布 `extension-v0.3.30`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.30.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.30-firefox.zip`。

---

## v0.3.76: 推荐卡片 hover 抖动修复（2026-05-18）

- 移除推荐卡片（`.recommendation-card`）hover 时的 `transform: translateY(-1px)`，消除大面积元素整体位移 + 内部按钮二次位移导致的视觉抖动；保留 `border-color` 与 `box-shadow` 过渡作为 hover 反馈。
- 浏览器插件版本提升到 v0.3.28，准备发布 `extension-v0.3.28`。

---

## v0.3.75: 配置保存生效与 LLM 路由修复（2026-05-18）

- `/api/config` 热重载后的 speculator tick 改为受 `BackgroundTaskRegistry` 管理的 detached task，保存配置不再等待一次可能很慢的 `force_tick()`；异常由 helper 记录并吞掉，避免后台补货失败反向影响配置保存响应。
- 浏览器插件配置保存请求新增 60s AbortController 超时，超时时显示 amber toast，提示“请求可能已写入，热重载可能仍在后台进行”，不再错误断言配置一定已落盘。
- 修复 [#12](https://github.com/whiteguo233/OpenBiliClaw/issues/12)：LM Studio 的 OpenAI-compatible `/v1/chat/completions` 不接受 `response_format={"type":"json_object"}`；v0.3.75 先对 LM Studio 默认本地端口改用通用 `json_schema`，并在其它兼容服务明确拒绝 `json_object` 时自动用通用 JSON schema 重试，避免初始化偏好分析阶段 400 后再误导性 fallback 到模板里的 Ollama `qwen2.5:7b`。v0.3.77 起 LM Studio 路径进一步调整为首次跳过 `response_format`，普通兼容服务仍保留 `json_schema` 重试。
- `[llm.soul]` / `[llm.discovery]` / `[llm.recommendation]` / `[llm.evaluation]` 覆盖现在真正进入运行时路由：`LLMService` 按内置 caller bucket（如 `recommendation.delight_score` → evaluation、`sources.xhs.*` → discovery）调用 `LLMRegistry.complete_provider()`，并用 per-call `model=` 覆盖 provider 模型而不污染 provider 实例默认值；override provider rate-limit / 错误不会偷偷 spill 到 default，未知或 embedding-only provider 只 INFO 一次后走默认链。
- `RuntimeContext`、`SoulEngine`、CLI builder、OpenClaw bootstrap 和 `SocraticDialogue` fallback 均接入 config-backed `module_overrides`，避免只在部分入口生效导致“配置保存了但实际调用没换模型”。
- 后端包版本提升到 v0.3.75；浏览器插件版本提升到 v0.3.27，准备发布 `extension-v0.3.27`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.27.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.27-firefox.zip`。

---

## v0.3.74: Config deadlock recovery（2026-05-17）

- `/api/config` 保存改为先校验再写盘，写入前生成 `config.toml.bak`，热重载失败时自动回滚；响应新增 `rollback_applied` / `restart_required`，避免错误配置把 daemon 卡进无法从 popup 修复的死锁。
- 配置保存会保留后端返回的 masked key、非空 `model/base_url/http_referer/x_title/reasoning_effort` 与 embedding 凭据；只有显式 `reset_fields` 才会清空允许列表里的 API Key，避免 settings UI 把真实 key 或模型名写成空值。
- FastAPI 生产启动遇到 `RegistryBuildError` 时进入降级模式：`/api/health`、`/api/config`、`/api/runtime-status` 和 `/api/runtime-stream` 仍可用，非配置接口返回 503；popup 可在离线缓存或降级配置页中保存修复配置，降级保存会提示重启。
- Popup 设置页缓存最近一次成功的配置快照；后端离线时可用缓存填表，后端降级时展示具体配置问题并把保存按钮切到“保存并提示重启”。
- 后端自动更新改为直接查询 GitHub `/tags` 并只接受 `backend-v*`（兼容 legacy `v*` / 裸 semver）作为后端版本来源，明确忽略 `extension-v*`；当 tag 列表里暂时没有 backend tag 时返回 `no_backend_tag_yet`，不再把扩展 release 误判成 "Already up-to-date"。
- LLM 结构化输出解析收敛到共享 helper，recommendation、delight、discovery eval-batch、awareness、insight、dialogue insight、profile builder 和 speculator 都能兼容 MiMo / 非 OpenAI provider 常见的 object wrapper、fenced JSON、JSONL、schema echo 与 malformed `{ [ ... ] }` 数组包裹。
- `embedding.provider="ollama"` 且 embedding `api_key/base_url` 为空时直接使用本地 Ollama 默认地址，不再发出向后兼容 credential fallback WARNING；远端 provider 仍保留一次性 warning。
- 文件日志 traceback 保留加回归测试锁定：rotating file handler、plain file handler 和配置热重载异常都会把 stack trace 写进文件日志。
- 后端包版本提升到 v0.3.74；浏览器插件版本提升到 v0.3.26，准备发布 `extension-v0.3.26`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.26.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.26-firefox.zip`。

---

## v0.3.73: Popup 运行时省钱开关（2026-05-17）

- Popup 顶部新增两个运行时开关：`暂停后台 LLM` 直接写入 `scheduler.enabled=false`，`关浏览器后暂停后台` 写入 `scheduler.pause_on_extension_disconnect=true`；设置页同步暴露后者。后端 `/api/config`、`config-show`、`start` / `serve-api` WARN 和 `config.example.toml` 都同步展示新字段。
- 后端新增 `PresenceTracker` 与共享 `background_llm_work_allowed()` gate：`scheduler.enabled` 是后台 LLM / embedding 总开关，`pause_on_extension_disconnect` 开启后还要求浏览器插件 `runtime-stream` 在线或处于断开宽限窗口。gate 覆盖 refresh、pool precompute、soul pipeline、xhs/dy producer、proactive push、AccountSyncService、startup one-shot 和 OpenClaw direct bootstrap；手动 CLI / API 操作不被隐式拦截。
- `/api/runtime-stream` 增加 reader / receive-side disconnect detector，浏览器 idle disconnect 后会正确触发 presence decrement，避免后端误以为插件一直在线；最后一个连接断开后按 `extension_disconnect_grace_seconds` 进入宽限。
- 浏览器插件版本提升到 v0.3.25，准备发布 `extension-v0.3.25`；Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.25.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.25-firefox.zip`。
- 文档同步更新 `docs/modules/config.md`、`docs/modules/cli.md`、`docs/modules/extension.md`、`docs/modules/integrations.md`、`docs/architecture.md`、`docs/spec.md`、README / README_EN 和配置样例，明确 pause gate 的范围是 daemon-owned background LLM / embedding work。

---

## v0.3.72: 浏览器插件后端端口可配置（2026-05-16）

- 负反馈消费链路收敛：`satisfaction_filter_enabled` 默认开启后只过滤 `quick_exit` 等被动 negative 事件，显式 `dislike` / `thumbs_down` 会保留给 `PreferenceAnalyzer` 作为 `disliked_topics` / 避让证据且禁止提取为正向兴趣；discovery 共享 `profile_summary`、推荐画像摘要和单条 / 批量推荐表达 prompt 现在都会带 `disliked_topics`，让 search / explore / trending query 生成、batch 内容评估和推荐文案都能避开长期雷点；awareness prompt 可生成“最近开始避开 X”的保守观察；B 站 content script 新增“不感兴趣 / 不喜欢 / 减少此类推荐”识别并规范化为 `feedback_type=dislike` 强信号。
- Discovery 画像上下文补齐：`build_profile_summary()` 不再只传兴趣标签、核心特质和避雷项，现在会把 `cognitive_style`、`values`、`motivational_drivers`、`current_phase`、`life_stage`、`mbti`、`source_platform_mix`、`recent_awareness`、`active_insights`、`style.quality_sensitivity` 以及兴趣的 `first_seen` / `last_seen` / `source` 一起带入 search / trending / explore / YouTube query 生成和内容评估 prompt；这样 discovery 可以同时理解“喜欢什么”“为什么喜欢”“最近在避开什么”和“当前阶段需要什么”。
- 浏览器插件设置页新增「后端端口」字段（默认 `8420`，仅接受 `1-65535` 的完整十进制整数）。Windows 启用 Hyper-V / WSL / Docker 后常见本地端口会被系统组件占用，导致 `openbiliclaw start` 默认 `8420` 启动失败；现在用户可改成 `18080` / `19090` / `13000` 等高位端口，并用 `openbiliclaw start --port <同一端口>` 启动后端即可继续使用插件。端口保存到 `chrome.storage.local`，不写入后端 `config.toml`。
- 新增 `extension/src/shared/backend-endpoint.ts` + `extension/popup/popup-backend-config.js` 共用 helper。`apiUrl()` / `wsUrl()` / `getBackendBaseUrl()` 在每次调用时解析当前端口，所以保存新端口后无需重载插件即可生效；service worker 通过 `chrome.storage.onChanged` 收到端口变更后会立即关闭旧 `runtime-stream` WebSocket 并按新 origin 重连。
- 同步收敛了之前散在 ~10 处的硬编码 `127.0.0.1:8420`：service worker、cookie 同步、xhs / dy / yt 任务派发、`_debug/log` 中继、抖音内容脚本现在都走 `apiUrl()` 统一解析。
- `manifest.json` / `manifest.firefox.json` 的 `host_permissions` 从固定 `127.0.0.1:8420/*` 放宽到 `127.0.0.1/*` + `localhost/*`，否则浏览器会在 manifest 层直接 block 非 `8420` 端口的请求；其他平台的 `*.bilibili.com` / `*.xiaohongshu.com` / `*.douyin.com` / `*.youtube.com` 权限完全不变。
- 浏览器插件版本提升到 v0.3.24，Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.24.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.24-firefox.zip`；`extension-v*` release workflow 现在会同时构建并上传这两个资产，避免 Firefox 用户只能从源码本地打包。
- 致谢 [@addtion99 #8](https://github.com/whiteguo233/OpenBiliClaw/pull/8) 提出端口可配置的需求并给出 popup 侧实现思路；本次以最小回归方式重做，扩展到 service-worker / dispatcher 全链路并补齐 manifest 权限。

---

## v0.3.71: Firefox 扩展构建与打包补强（2026-05-16）

- Eval-batch 负样本锚定：`discovery/engine.ContentDiscoveryEngine._evaluate_batch` 现在每批前通过新 `_get_negative_exemplars()` 从事件层拉最近 8 条 negative 标题（来自 `soul/negative_exemplars.py` 的 recency-weighted 去重列表，半衰期 14d，标题超过 80 字会带 `…` 截断），引擎内部有 5 分钟 / `latest_event_id` 双失效 TTL 缓存避免 back-to-back batch 重复查 SQLite；batch 评分缓存 key 也带最新 event id，确保新 quick-exit / explicit-negative 出现后不会继续复用旧分数。`build_batch_content_evaluation_prompt` 新增可选 `negative_examples` kwarg，在 `<source_context>` 与 `<content_batch>` 之间插入块，并在 `_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT` 永久加入两条规则（10 / 11）让 LLM 按话术 / 商业意图 / 标题结构层面 pattern-match 候选与示例，而不是关键词重叠。配合上文事件满意度信号，分类先跑、负样本池自动建立，evaluator 不需要等到 `satisfaction_filter_enabled` 打开就能开始压制"同款保姆级全攻略 / 同款月入过万钓贴"类候选。Cold-start 用户（没有 negative 分类事件）保持 user prompt 字节形态不变，cache prefix 不被打断。
- 事件满意度信号（默认关闭）：每条行为事件在 `Database.insert_event` 写入时由 `classify_event_satisfaction`（`sources/event_format.py`）打上 `inferred_satisfaction`（positive / neutral / negative / unknown）和 `satisfaction_reason`（`explicit_engagement` / `meaningful_dwell` / `quick_exit` / `explicit_negative` / `passive_browse` / `missing_dwell` / `fallback`）；`events` 表加列、加 additive 迁移、加 `query_events(satisfaction_modes=...)` 过滤。扩展 `video-dwell-tracker.ts` 在 SPA 路由切换 / `pagehide` 时 flush 一个 `click` 事件，metadata 携带 `watch_seconds` / `video_duration_seconds`，区分 meaningful_dwell vs quick_exit。新增 `soul/event_filters.py` 与 `SoulPreferenceConfig.satisfaction_filter_enabled`（默认 `false`），`PreferenceAnalyzer` 在开关打开后只 drop negative 事件（quick_exit / explicit_negative），保留 positive / neutral / unknown 上下文，断开"标题党点击 → 偏好层把它当深度兴趣"的自喂回路。Rollout 安全：开关默认关，分类先跑一两个版本观察 `inferred_satisfaction` 分布再切。
- 觉察弹性补强：`AwarenessAnalyzer._coerce_note_list` 在前述 `results/items/...` wrapper 基础上再扩展到 `observations / recent_observations / latest / latest_observations`，并兼容 reasoning 模型常见的 bare singular-note dict（仅需 `observation` 字段）与 wrapper-key 下的单 note dict；`CognitionCycle._run_awareness` 失败时单次 2s 间隔重试，仍失败则记 WARNING 且**不推进** `last_awareness_at`，下一 tick 立即重试而非空等 12h；`build_awareness_prompt` 的 system 内容 / user 块顺序 / sort_keys 形态由 `tests/test_llm_prompts.py` 三组 byte-equal 回归测试锁死。修复 MiMo 后端 6 小时连发 569 条 `Awareness analyzer failed during cognition cycle` 的退化路径。
- LLM prompt-cache 稳定性补强：`AwarenessAnalyzer` 现在接受 `{"results":[...]}` / `{"items":[...]}` 等 object-wrapped array 响应，避免 MiMo 等模型 JSON mode 包裹数组时中断觉察生成；`build_awareness_prompt` 与 `build_batch_content_evaluation_prompt` 的 user prompt 改为稳定画像在前、来源与本批数据在后，并使用确定性 JSON，提升 `soul.awareness` / `discovery.evaluate_batch` 的缓存前缀复用。
- 安装与诊断补强：`install.sh` / `install.ps1` / `agent_bootstrap.py` 会把 `localhost,127.0.0.1,::1` 写入 `NO_PROXY/no_proxy`，避免 Windows 全局代理劫持本地 health check；OpenAI-compatible provider 会记录 HTTP 400 响应体摘要，便于定位 MiMo 请求 schema 错误；B 站 `/nav` 返回 `-101` 时现在抛出 `BilibiliAuthExpiredError` 并明确提示重新登录或保持扩展在线同步 Cookie。
- 测试与类型基线恢复：修复 `DelightWeights` 测试遗漏 `likes` 权重、discovery 评估缓存 key 与当前 content identity 不一致、pipeline fake 画像 prompt 识别失效，以及 `CognitionCycle` 只因 preference 空而跳过的过宽 gate；补齐 eval / OpenClaw / source adapter 的 JSON 类型守卫和 optional dependency 动态导入边界，使 `pytest` 全量与 `mypy src/` 重新通过。
- 浏览器扩展新增 Firefox 140+ 支持：新增 `manifest.firefox.json` 使用 `sidebar_action` 替代 Chrome 的 `sidePanel`，`npm run build:firefox` / `npm run package:firefox` 产出独立 `dist-firefox/` 和 `openbiliclaw-extension-v*-firefox.zip`；`openExtensionUi()` 增加 Chrome sidePanel -> Firefox sidebarAction -> tab 的三段降级。Firefox manifest 的 version 在构建时从 `manifest.json` 注入，并声明 AMO 所需 `data_collection_permissions`；Chrome / Firefox 打包前都会删除旧 zip，避免本地重复打包残留过期文件。Chrome / Edge / Brave 构建路径完全不变。
- 浏览器插件版本提升到 v0.3.23，承载 Firefox 140+ 支持与上文「视频停留满意度采集」（`video-dwell-tracker.ts`：SPA 路由切换 / `pagehide` 时 flush `click` 事件携带 `watch_seconds` / `video_duration_seconds`），同时避免复用已发布的 `extension-v0.3.22` tag / release 资产语义。Chrome / Edge / Brave 走 `openbiliclaw-extension-v0.3.23.zip`，Firefox 140+ 走 `openbiliclaw-extension-v0.3.23-firefox.zip`。
- README / README_EN 顶部 highlights callout 收敛为“只保留最新版本、≤4 条、≤1 句、CN/EN 同步”，完整历史继续放在 changelog，避免 README 顶部堆成迷你变更日志。
- README 增加用户交流群二维码入口，放在贡献入口前，避免打断首次安装路径。
- README / README_EN 底部“更新日志 / Release History”从长版本表收敛为最新版本入口 + 完整 changelog / Releases 链接，避免 README 主体被历史记录撑长。

---

## v0.3.70: 修复扩展未启动后端时 WebSocket 报错（2026-05-16）

- 修复 [#7](https://github.com/whiteguo233/OpenBiliClaw/issues/7)：扩展 service worker 连接 `/api/runtime-stream` 之前先做一次 2 秒超时的 HTTP `GET /api/health` 健康探针，只有后端可达才 `new WebSocket(...)`。fresh-install 用户只装扩展、未启动 `openbiliclaw start` 时，`chrome://extensions` 不会再被浏览器层的 `WebSocket ... ERR_CONNECTION_REFUSED` 计入「错误」徽标；健康探针失败仍走 5s → 60s 指数退避兜底重连，后端起来后自动恢复。
- 后端不可达时在扩展工具栏图标上打一个浅灰 `!` badge 作为可视提示，WebSocket 首次连上后自动清除；popup 内继续显示「后端还没开张，先运行 `openbiliclaw start`」。
- 浏览器插件版本提升到 v0.3.22 并准备发布该修复。

---

## v0.3.69: 抖音首页推荐流 discovery（2026-05-12）

- Gemini provider 在 json_mode 下识别 reasoning-first 模型（`gemini-3.x` / `gemini-2.5-pro*`）并跳过 `thinking_budget=0` 优化，避免 `gemini-3.1-pro-preview` 等模型被 Google API 以 `400 INVALID_ARGUMENT` 拒绝；`gemini-2.5-flash` 的省钱通路保持原样。同时补全 pricing 别名（`gemini-3.1-pro-preview` / `gemini-3-pro-preview`），CLI / config / 文档统一改用真实模型 ID 并标注 Public Preview 需付费项目。
- 兴趣探针新增本地 novelty guard：LLM 生成和 PreferenceAnalyzer seed 注入都会对照现有画像 domain / specifics、active/cooldown 猜测和近期 probe history 做规范化字符串 + 中文 bigram 去重，避免把已知画像细项换皮成新探针；active pool 多样性选择也会参考已有 active 体验轴。
- probe 近期历史补齐持久化：`discovery_runtime_state` 现在保存 `probed_axes`，OpenClaw `next-probe` 成功返回后也会记录 domain / axis，连续调用不再重复拿同一条 active probe。
- probe 显式反馈纳入历史治理：`/api/interest-probes/respond` 现在记录 `probe_feedback_history`，后续 LLM 生成、PreferenceAnalyzer seed、runtime push 和 OpenClaw `next-probe` 会避开 reject / chat_negative 明显重复的方向，并降低负向反馈体验轴的入池/推送优先级。
- 搜索词生成 prompt 新增 Rule 10：禁止从 `favorite_up_users` 创作者名字推断其内容类型作为 query 主题，避免跨平台关注的作者（如抖音耽美作者）泄漏到 B 站搜索发现。
- pool_source_shares 多源配比修复：`[sources.xiaohongshu]` 新增 `enabled` 字段（默认 `true`，init 选 No / `--no-xhs` / `OPENBILICLAW_NO_XHS=1` 会写回 `false`），关闭后 XhsTaskProducer 不再吃 `daily_search_budget` 跑空；`[sources.youtube]` 新增 `enabled` 字段（默认 `false`）；`runtime_context._pool_source_shares_from_config` 会按 `enabled` 剔除被关闭源的份额，让 bilibili 自动吃下剩余配额而不是把池子卡在 540/600；`_pool_source_family` 识别 YouTube `yt_search` / `yt_channel` 等来源；controller 启动时若发现仍有"有配额但 producer is None"的源，会 warn 一次。
- source policy 控制面补齐：`[scheduler.pool_source_shares]` 默认保存 B 站 / 小红书 / 抖音 / YouTube = `8 / 1 / 1 / 1`，但 runtime / OpenClaw 都只使用按 `sources.<platform>.enabled` 剔除后的有效配比；`init` 会写回小红书 / 抖音 / YouTube 开关，并在采集完事件后按各平台事件量推荐比例让用户确认或手填；`/api/config/source-share-suggestion` 与插件设置页可按已有事件重新生成建议比例。
- 插件设置页的“按已有信号建议比例”修复为按当前页面尚未保存的平台开关 / 比例 POST 生成，避免按钮因 `setVal` 作用域错误点击失败，也避免先勾选或关闭渠道后仍按旧保存配置给建议值。
- Chrome 插件版本提升到 v0.3.21，随设置页比例建议 POST 修复重新发布；后端包版本对齐当前 v0.3.69 changelog，便于同步分发新的 `/api/config/source-share-suggestion` POST 能力。
- Chrome side panel 聊天改为 durable turn：新增后端 `/api/chat/turns` 创建 / 查询接口和 SQLite `chat_turns` 表，popup 主聊天、惊喜推荐内聊和兴趣猜测内聊都会先写入 `pending` 再轮询完成；Chrome 切 tab、reload 或丢弃不可见 side panel 后可恢复消息、thinking 占位和已完成回复。
- 插件设置页与后端配置 schema 对齐：新增 DeepSeek reasoning、OpenRouter headers、per-module LLM override、B 站 / sources 浏览器配置、小红书 / 抖音预算、数据目录 / SQLite、scheduler 高级项、候选池平台配比、自动更新和 logging 清理参数，并通过 `/api/config` 完整读写。
- `/api/config` 现在暴露并保存 `sources.*`、scheduler speculation / `pool_source_shares` / auto-update interval、logging rotation / unmanaged cleanup 和 `llm.deepseek.reasoning_effort`；`save_config()` 同步串行化这些隐藏高级字段，避免插件保存常用项时把它们丢回默认值。
- 配置默认值文档和示例补齐：`discovery_cron` 统一为 `"0 */8 * * *"`，`auto_update_enabled` 统一为保守默认 `false`，配置参考移除已废弃的 `[sources.xiaohongshu].sidecar_url`，并补上 YouTube / XHS / Douyin init 环境变量说明。
- YouTube 已接入首次 `init` 的多源画像链路：交互式 `--yes-youtube` / `--no-youtube` 决策、`OPENBILICLAW_NO_YOUTUBE=1` 环境跳过、浏览器扩展 `yt_tasks` 串行拉取观看历史 / 订阅 / 点赞，并把事件送入 `analyze_events()` 与 `build_initial_profile()`。
- YouTube discovery 真实 smoke 补强并修复集成问题：`yt_search` 现在正确解析真实 `LLMService` 返回的 `LLMResponse.content` 作为搜索关键词，`yt_channel` 可从真实 YouTube follow 事件里的频道 URL 拉取最新视频并在 `scrapetube` 失效时使用 `yt-dlp` fallback，`ContentDiscoveryEngine` 改为按跨源 `source_platform + content_id` 去重 / 缓存，避免多个 YouTube 候选因空 `bvid` 被合并。
- `yt_trending` 增加真实网络 fallback：当 YouTube 当前 `FEtrending` InnerTube browseId 返回 400 时，改为抓取公开 topic 页（gaming / sports / news / podcasts / live）的 `ytInitialData` 视频并继续进入 LLM 打分，真实 smoke 已从 `fetched=0` 恢复为可产出候选。
- 新增 YouTube 单源工具：`openbiliclaw fetch-youtube` 用于 smoke 浏览器扩展任务桥，`openbiliclaw import-youtube <path>` 支持 Google Takeout `.zip` 或目录导入观看历史 / 订阅 / 点赞。
- 新增 GitHub Pages 项目主页：`docs/index.html` 作为 `/docs` 发布入口，首屏突出纯本地 / 私有 / 开源 / 自进化跨平台内容发现 Agent 定位，并提供一句话安装提示、Chrome 插件下载、GitHub 源码、产品闭环和推荐 / 价值画像 / 认知风格 / 聊天校准截图；原文档导航保留在 `docs/index.md`。
- GitHub Pages 项目主页新增中英文双语切换：默认跟随浏览器语言，用户手动选择后写入 `localStorage`，安装提示、导航、CTA、截图说明、架构说明和复制按钮状态均同步切换。
- Chrome 插件版本提升到 v0.3.20 并准备发布：打包这几天已合入的抖音任务桥、Douyin search / hot / feed 插件签名链路、抖音 Cookie 同步和小红书 / 抖音 dispatcher 互斥，manifest 描述同步改为跨平台内容发现 Agent。
- README / README_EN 顶部新增项目主页入口，直接链接到 `https://whiteguo233.github.io/OpenBiliClaw/`。
- README / README_EN 快速开始重排：普通用户路径收敛为“安装插件 → 复制一句话给 AI 助手部署后端 → 在同一浏览器登录内容平台”，脚本、Docker、多源登录说明、本地 embedding 和 discovery 调试命令统一移入高级折叠项，减少首次安装时的干扰信息。
- 修正 CDP 文档定位：小红书和抖音当前稳定链路都走 Chrome 插件任务，不再在 README、Docker 部署文档和配置参考里推荐用户为这两个源额外启动 CDP 调试 Chrome；`[sources.browser].cdp_url` 保留给通用 Web / 自定义网页源。
- 新增抖音首页推荐流 discovery：`discover-douyin --source feed` 会入队 `dy_tasks(type="feed")`，扩展在已登录抖音首页通过 MAIN-world `byted_acrawler.frontierSign()` 签名 `/aweme/v1/web/tab/feed/`，候选以 `dy-plugin-feed` 进入 discovery。
- 抖音公开 discovery 子来源调整为 `search` / `hot` / `feed`；`creator` 不再作为 CLI 可选渠道，避免把作者主页时间线当作默认内容发现来源。
- `[sources.douyin]` 新增 `daily_feed_budget`，限制每日 `dy_tasks(type="feed")` 入队次数；`daily_search_budget` / `daily_hot_budget` 继续分别约束 search / hot。
- 新增 `[scheduler.pool_source_shares]` 平台级候选池配比配置，默认 B 站 / 小红书 / 抖音 = 8 / 1 / 1；`pool_target_count=600` 时目标为 `bilibili=480`、`xiaohongshu=60`、`douyin=60`。
- runtime refresh 改为按平台族统计和修剪候选池：B 站四个策略统一计入 `bilibili`，小红书 `xhs-extension-*` 计入 `xiaohongshu`，抖音 `dy-plugin-*` 计入 `douyin`；小平台低于配额时会保护 / 复活其候选，平台族超过配额时即使总池子未满也会先压回配额内。
- discovery LLM 评估增加池子容量感知：runtime 会按 B 站平台缺口而不是总池子缺口决定本轮 limit；`search` / `trending` / `related_chain` / `explore` / `douyin_direct` 在送 LLM 前会把候选窗口收缩到 `max(12, limit*4)`、上限 90，避免只缺少量候选时仍评估几十条并随后立刻 suppressed。
- discovery batch 评估解析补强：兼容 provider 回显输入 JSON 后再输出结果、Markdown fenced JSON，以及一行一个 JSON object 的 NDJSON 结果，避免 batch 解析失败后退回 N 次单条 LLM 评估。
- 小红书 / 抖音 bootstrap task-result 的新增事件现在不只落 memory：profile 已初始化后会转成 `ProfileSignal` 进入 `ProfileUpdatePipeline`，让后续拉到的收藏 / 点赞 / 关注事件参与增量画像更新；首次 init 仍由 `analyze_events()` + `build_initial_profile()` 统一处理，避免重复学习。
- 小红书 `bootstrap_profile` 加入近期任务复用和领取态防重：`init --yes-xhs` / `fetch-xhs` 默认复用 6 小时内的 pending / in-progress / completed / failed bootstrap 任务，避免反复打开前台 tab 拉收藏 / 点赞；扩展通过 `/api/sources/xhs/next-task` 取任务时会把任务原子标记为 `in_progress`，15 分钟无回写才允许重新领取。需要强制重拉可用 `openbiliclaw fetch-xhs --force` 或把 `OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS=0`。
- 抖音 discovery 插件任务改为后台 tab：`dy_tasks(type="search"|"hot"|"feed")` 仍复用登录浏览器签名桥，但 `chrome.tabs.create({active:false})` 执行，不再抢用户焦点；只有显式导入用户事件的 `bootstrap_profile` 继续以前台 tab 运行。
- 初始化偏好分析的并发分片增加容错：当某个分片被 LLM 风控拒绝或返回非 JSON 时，会递归拆小定位问题事件，最终只跳过仍失败的单条事件，避免一个标题导致整次 `init` 中断；provider / 网络错误仍会正常失败并暴露。
- 初始化画像生成增加 compact retry：首轮 `history_summary` 触发模型风控或坏 JSON 时，会移除原始标题 / context 后用结构化偏好、来源分布、觉察和洞察重试一次，避免真实多源初始化在最后画像阶段被单个高风险标题中断。
- `ProfileBuilder` 的画像长度校验上限从 320 放宽到 500 字：prompt 仍要求 150-260 字，但真实模型偶尔会返回 330 字左右的有效画像，不再因为轻微超长让完整 init 失败。
- `ProfileBuilder` 对画像辅助字段更容错：`core_traits` / `cognitive_style` / `motivational_drivers` / `values` / `deep_needs` / `life_stage` / `current_phase` 缺失或列表格式轻微不符时会保守补空值并记录 warning，不再因为单个辅助字段漏吐中断首次初始化。
- `openbiliclaw init --yes-douyin` 完成摘要现在会把抖音信号也写进“本次画像综合了...”提示；只启用抖音或同时启用小红书 / 抖音时，不再错误显示“两个平台”且漏掉抖音。
- 一句话安装的 auto-init 现在会在原样输出 `openbiliclaw init` 日志的同时，额外发 `BOOTSTRAP_STATUS status=progress message=init_progress` 结构化事件；AI agent 可实时提示 1/4、2/4、3/4、4/4 和补货阶段进度，不必等最终 `init_complete`。
- 新增 runtime `DouyinDiscoveryProducer`：当抖音低于平台配额且 `[sources.douyin].enabled=true` 时，后台通过 `DouyinDiscoveryService(cache=True)` 复用 search / hot / feed 插件签名链路补池。
- 修复 B 站 Cookie 自动同步后的后台循环丢失：`/api/bilibili/cookie` 热重载 runtime 后会重新启动 refresh / account sync / auto update 任务，避免扩展首次同步 Cookie 后把小红书与抖音 producer 停住，导致抖音配额长期为 0；重复同步相同 Cookie 时保持幂等，不再反复 hot-reload 打断抖音 discovery 等待。
- 抖音插件 discovery 入队前会清理过期的 search / hot / feed pending 任务，避免旧版本重复 hot-reload 留下的陈旧队列挡住当前 producer，导致新任务等到超时才回退。
- discovery engine 注册同名 strategy 时改为替换旧实例，避免 runtime `DouyinDiscoveryService(cache=True)` 每轮追加一个新的 `douyin_direct`，导致后续一次抖音 discovery 同时跑多个相同 search 任务、快速耗尽 `daily_search_budget`。
- B 站 `SearchStrategy` 的专用 search client 现在会继承运行时 B 站 Cookie：真实 smoke 发现匿名 WBI search 稳定返回 `data.v_voucher`，而同一签名请求带有效 Cookie 可正常返回 `result`；保留独立 client 降低 session 串扰，但不再丢认证态。
- 抖音扩展 search 任务的单关键词超时窗口从 60 秒放宽到 180 秒，后端 runtime / CLI 默认等待窗口同步为 180 秒；真实 smoke 显示搜索页导航到 `DY_SEARCH_EXECUTE` 可能已消耗 100s+，旧 120s 会在 search API bridge 返回前先触发 `task_timeout`。
- runtime 抖音 producer 每轮只取 1 个画像关键词做 search，然后继续跑 hot / feed，避免后台补池在多个搜索关键词上串行等待插件超时并消耗过多 search budget；CLI `discover-douyin` 仍可按显式关键词调试多 search。
- runtime 补池进一步收敛无效成本：B 站四策略共享同一个平台缺口预算并通过 `strategy_limits` 分摊到各策略，手动 refresh 也复用同一套平台缺口计划；小红书 producer 会按小红书缺口减少本轮关键词数；抖音 producer 在小缺口时优先 feed / hot，只有缺口较大才恢复 search；各策略送 LLM 评估前的窗口从 `max(12, limit*4)` 收紧到 `max(6, limit*2)`、上限 90。
- 新增 pool distribution snapshot 基础模型：`PoolDistributionSnapshot` 汇总候选池总量、平台族数量 / 缺口和 topic/style/franchise 饱和方向，并通过 `Database.get_pool_distribution_counts()` 复用 fresh、非 dislike、未推荐且可打开的候选统计口径；默认饱和阈值为 topic `max(8, pool_target_count // 20)`、style `max(12, pool_target_count // 8)`、franchise 10，且 `source_deficits` 明确保持为平台 / 来源缺口信号，不混入内容轴。
- runtime refresh 现在会在 B 站 discovery 前 fail-soft 构建 pool snapshot，并通过 `ContentDiscoveryEngine.discover(..., pool_snapshot=...)` 兼容转发给支持该参数的主策略与 backfill 策略，旧版 strategy 签名保持可用。
- `SearchStrategy.discover(..., pool_snapshot=...)` 现在会把 `PoolDistributionSnapshot.to_prompt_hints()` 注入搜索 query prompt：对已拥挤 topic/style/franchise 做软避让，显式 `undercovered_axes` 可形成 `prefer_axes`；运行时快照暂不把平台名转成内容 `prefer_axes`，且坏 hint 会被丢弃后继续走正常 LLM query 生成。
- discovery engine 会在最终压缩和入池前应用 pool snapshot 软重排：饱和 topic/style/franchise 轻微降权，undercovered axes 轻微加权，强相关候选保留优先级且原始 `relevance_score` 不被改写；推荐 serving 路径保持从 `content_cache` 取已预生成候选不变。
- 抖音补池预算修正：`dy_tasks` 中因 daemon 重启 / 插件未及时消费而失败的 `stale_pending` discovery 任务不再计入 search / hot / feed 每日预算，避免历史陈旧 pending 吃光当天 search 配额。
- 抖音 runtime 大缺口补池改为优先 `search` / `hot`，不再把低产出的 `feed` 混进大批量补池；`daily_hot_budget` 在 runtime 中会按本轮抖音缺口动态抬高到最多 60，默认 `5` 仍作为小缺口 / 手动调试的保守基线。
- 参考开源实现确认首页推荐流端点：F2 暴露 `fetch_post_feed` + `TAB_FEED=/aweme/v1/web/tab/feed/`，Douyin_TikTok_Download_API 也记录了 `TAB_FEED` 和 `PostFeed` 参数模型；本项目不引入第三方依赖，只复用端点和参数形态。
- 优化抖音 hot discovery 稳定性：hot 插件任务现在带总目标 `max_items`，累计达到目标即提前结束；后端小批量 hot 请求只展开少量 hot seed，避免 `--limit 3` 为了 3 条候选串行打开 3 个 `/hot/{sentence_id}` 页面并撞上 `task_timeout`。
- 文档同步补齐抖音事件与 discovery：README / README_EN、一句话安装、agent 部署、OpenClaw quickstart 和 discovery 模块文档都更新为抖音 search / hot / feed、`--yes-douyin` / `--no-douyin`、`BOOTSTRAP_STATUS init_progress` 的当前行为。

---

## v0.3.68: 抖音插件搜索 smoke 跑通（2026-05-11）

- 新增 `openbiliclaw search-douyin` 独立命令：CLI 入队 `dy_tasks(type="search")`，浏览器扩展在已登录抖音会话中打开搜索页，回传 `dy_search` 候选，便于单独调试抖音搜索 discovery 召回。
- 抖音扩展任务桥新增 search 类型：background dispatcher 支持关键词队列、逐词执行、partial + final 回写；后端保留搜索结果在 `dy_tasks.result_json`，不会传播成初始化画像事件，避免把 discovery 候选误当用户行为。
- 修复插件搜索 0 结果问题：MAIN-world search API bridge 现在使用完整浏览器参数，并调用页面 `byted_acrawler.frontierSign()` 给搜索 URL 追加 `X-Bogus`；主搜索端点有结果时不再继续打 fallback 端点。
- 修复抖音插件搜索偶发 `task_timeout`：dispatcher 等待抖音首页 / 搜索页 ready 时，除了监听 `chrome.tabs.onUpdated(status=complete)`，也会在 tab 已经 complete 或抖音 SPA 没有再发 complete 事件时走 fallback，避免任务停在 `/jingxuan` 不继续跳搜索页。
- `discover-douyin --source search` / `discover --source douyin` 的 search 子来源现在优先复用插件签名搜索链路，候选以 `dy-plugin-search` 写入 discovery 结果；插件任务空 / 失败时再回退 direct-cookie search。
- `discover-douyin --source hot` / `discover --source douyin` 的 hot 子来源改为插件 hot-related 链路：后端先从 hot board 取 `sentence_id`，扩展打开 `/hot/{sentence_id}` 解析跳转后的 seed aweme，再用页面 acrawler 签名 `/aweme/v1/web/aweme/related/`，候选以 `dy-plugin-hot-related` 进入 discovery；插件空结果时再回退 direct-cookie hot。
- `[sources.douyin].daily_hot_budget` 现在实际限制 `dy_tasks(type="hot")` 入队次数，`daily_search_budget` 继续限制 search 插件任务。
- 真实 smoke：关闭旧临时未登录 Chrome 干扰后，`openbiliclaw search-douyin -k 猫 --max-items-per-keyword 10 -w 180` 拉到 10 条候选。
- 真实 smoke：`openbiliclaw discover-douyin --source search --keyword 猫 --limit 5 --no-cache --no-evaluate` 拉到 5 条 `dy-plugin-search` 候选。

---

## v0.3.67: 抖音收藏/点赞拉取 E2E 补强（2026-05-09）

- 新增抖音 direct-cookie discovery 设计与首批实现：`discover --source douyin` 可在 `[sources.douyin].enabled=true` 且存在环境变量覆盖或扩展同步 Cookie 时拉取 `dy-direct-search` / `dy-direct-hot` / `dy-direct-creator` 候选，并按 `source_platform="douyin"` 写入 discovery pool；初始化画像仍保留扩展路径。
- 浏览器扩展新增抖音 Cookie 自动同步：service worker 读取 douyin.com Cookie 后 POST 到 `/api/sources/dy/cookie`，后端保存到 `data/douyin_cookie.json`；`discover --source douyin` / `discover-douyin` 现在按“环境变量覆盖 → 扩展同步文件”解析 Cookie，不再要求普通用户手动导出。
- 抖音 Cookie 同步门槛从“必须有 `msToken`”放宽为“存在登录态 / session / passport 类 Cookie 即同步”：真实 Chrome 登录态可能只有 `sessionid` / `sid_guard` / `ttwid` / `odin_tt` 等 Cookie，扩展会完整同步 header，让 direct discovery 自己通过 smoke 判断有效性。
- 扩展 Cookie alarm 兜底同步现在同时刷新 B 站和抖音 Cookie：后端重启、runtime-stream 短暂断开或用户登录态早已存在时，不再只补发 B 站 Cookie。
- 抖音 direct-cookie 请求遇到连接异常时改为软失败返回空结果并记录日志，避免 `discover-douyin` 在单次网络抖动时直接 traceback。
- 抖音 creator discovery 增加最近 bootstrap 作者兜底：不显式传 `--creator-sec-uid` 时，会先读 `OPENBILICLAW_DOUYIN_CREATOR_SEC_UIDS`，再从最近完成的抖音发布 / 收藏 / 点赞 / 关注任务结果里提取 creator `sec_uid`，优先用 creator timeline 拉公开视频，避免 search / hot 软返回空列表时默认 discovery 只能产出 0 条。
- 抖音 discovery 抽成独立 `DouyinDiscoveryService`：CLI、runtime 或未来 API 都可以复用同一服务；新增 `openbiliclaw discover-douyin` 独立调试命令，支持指定关键词、creator sec_uid、子来源，并可用 `--no-cache --no-evaluate` 直接查看源接口召回。
- 抖音扩展 MAIN-world API harvester 增加可测试导出，并补齐收藏 / 点赞分页桥接单测，覆盖 `dy_collect`、`dy_like` 从页面 API 到 isolated world 的 postMessage 路径。
- 后端 `/api/sources/dy/task-result` 增加真实 dispatcher 形态回归：各 scope 以 `partial` 分批回传 videos，最终 `ok/empty` 完成任务时保留已回传视频、去重并完成任务。
- CLI 增加 `init --yes-douyin` 对接测试，确认抖音事件会进入 `analyze_events()` 与 `build_initial_profile()`；同时明确 `fetch-douyin` 仍是纯拉取命令，不会隐式重建画像。
- 小红书 / 抖音 bootstrap collect 默认等待统一到 `180s`：`init --yes-xhs --yes-douyin` 连续跑两源时，小红书有更长窗口结束前台 tab 任务，降低超时后立刻启动抖音造成焦点竞争的概率；`fetch-xhs` / `fetch-douyin` 默认 smoke 窗口也同步为 `180s`。
- `agent_bootstrap.py` / 一句话安装脚本增加 `--yes-douyin` / `--no-douyin` 显式决策透传；README、CLI、Soul、架构、Docker 和 agent 安装文档同步记录抖音 init 数据流。

---

## v0.3.66: 修复 pool 上限失守（refresh 结束时漏 enforce 总量 cap）（2026-05-08）

### 背景

线上 popup 看到 `pool_available_count = 668`，配置里 `pool_target_count = 600`，明显超量。日志里看到 `_enforce_pool_cap` 在 04:25:58 把 pool 砍到 556 之后整整 10+ 分钟没再跑，期间 daemon 一直在跑 discovery（一堆 `discovery.evaluate_single` LLM 调用），pool 静默从 556 涨回 668。

### Root Cause

`_run_refresh_plan`（discovery 主流程）跑完一轮后只调了三个 trim：
- `trim_explore_cluster_overflow`（每个 explore cluster 不超过 N 条）
- `trim_topic_group_overflow`（每个 topic_group 不超过 pool_target / 10）
- `evict_stale_pool_items`（按 14 天年龄淘汰）

**这三个都是按"维度"砍，不卡总量**。所以一轮 discovery 完成时，每个维度都在配额内，但加总可以远超 `pool_target_count`。每个 strategy 内部 LLM 评估一批就往 `content_cache` 写一批 `pool_status='fresh'`；strategy 之间的 `if current_pool_count >= self.pool_target_count: break` 只防止**启动新 strategy**，对单个 strategy 内部的溢出无效。

`_enforce_pool_cap`（按总量砍）虽然存在，但只在 `run_forever` 的周期性 tick 里跑。当 discovery 持续 10-30 分钟时（v0.3.47 起，LLM eval batch 可能更慢），周期性 tick 被压住，pool 一路涨。

### 修复

`runtime/refresh.py::_run_refresh_plan` 末尾、状态写入之前，加一次 `self._enforce_pool_cap()`。这条路径已经做齐了：
1. `trim_topic_group_overflow`（再跑一遍）
2. `reactivate_under_quota_pool_sources`（按 source family 配额复活 suppressed 中可恢复项）
3. 第二次 `trim_topic_group_overflow`
4. 总量 trim 到 `pool_target_count`（`trim_pool_to_target_count`）

也就是说每轮 discovery 完成后 pool 必然 ≤ target，popup 不会再看到超量。

### 测试

- `test_run_refresh_plan_enforces_cap_when_discovery_overshoots` 复现 bug：discovery 单次 push 25 条把 pool 从 25 推到 50（target=30），断言 force_refresh 完成后 `pool_count <= 30`
- `test_run_refresh_plan_stops_midway_when_cap_hit` 等既有 37 个 refresh runtime 测试全部通过，无回归

### 影响

- 用户看到的"还有 N 条可换"不会再超过 `pool_target_count`
- 长跑 discovery 期间 pool 也守得住（不再依赖 run_forever 周期性兜底）
- 没 schema 改动，只是多调一次现成 helper，性能开销可忽略（一次 SQL group-by + 至多一次 UPDATE）

---

## v0.3.65: 修复 speculator 滞留 bug（confirmed 占满 active 槽位导致探针卡死）（2026-05-08）

### 背景

线上观察到 `openbiliclaw probe` 显示「暂时没有活跃的猜测」，但 `force_tick` 仍然返回 `generated=0`。dump `data/memory/speculative_state.json` 后看到 `active` list 里 5 项全是 `status="confirmed"`（不是 `"active"`），把 `max_active=5` 的额度全占满了 —— LLM 调用确实跑了、返回了 7 个候选、quality gate 也都过了，但 `_generate` 内部 `if len(state.active) >= self._max_active: break` 永远立即触发，一个候选都 append 不进去。

### Root Cause

状态机本来设计是：
- `active` → 信号累积满 threshold → `promote_ready` 搬到 promoted 列表 → pipeline 加进 profile.likes
- `active` → 用户确认（CLI/popup） → `confirmed`（`user_confirm_speculation` 同时把 `confirmation_count` 设为 threshold）
- `active` → 用户拒绝 → `rejected` 进 cooldown
- `active` → TTL 过期 → `rejected` 进 cooldown

**但** `promote_ready` 只匹配 `status == "active"`，`expire_stale` 同样只处理 `"active"`。所以 `status="confirmed"` 的项进了**死循环**：
- `promote_ready` 不收（status != "active"）
- `expire_stale` 不收（status != "active"）
- `_generate` 把它们计入 `len(state.active)` 触发满员判断 → 阻塞新生成

用户每多 confirm 一个就多一个永远不动的尸体，最终 active list 撑满后**整个探针生成链路就卡死**。

### 修复

`speculator.py::promote_ready` 加一条 OR 分支：

```python
ready = (
    spec.status == "active"
    and spec.confirmation_count >= spec.confirmation_threshold
) or spec.status == "confirmed"
```

这样两条 promote 路径汇聚到同一个出口：自然累积到阈值的 + 用户主动确认的，都从 `state.active` 搬出 → pipeline 自动加到 `profile.interest.likes`。

### 测试

新增两个回归 case 在 `tests/test_speculator.py`：
- `test_promote_ready_handles_user_confirmed_status` — 单元层面验证 confirmed + active(threshold met) 两条路径都被正确收割
- `test_force_tick_unblocked_when_active_full_of_confirmed` — E2E 复现报告场景：5 个 confirmed 占满 active 时，下次 force_tick 必须 (1) 把 5 个全部 promote (2) 在腾出的槽位生成新猜测

### 影响

- 已有用户 `data/memory/speculative_state.json` 里如果有滞留 confirmed 项，下次 daemon 跑 speculator tick 时会被自动清理 + 加进 `profile.interest.likes`。本次修复同时补做了之前漏掉的"晋升进正式兴趣"动作 —— 用户曾经手动 confirm 过的猜测方向终于会落到画像里。
- 没有 schema 改动，state.json 文件格式不变。

---

## v0.3.64: 小红书 bootstrap 拉取上限 50 → 300 (2026-05-06)

### 背景

XHS bootstrap 的 `max_items_per_scope` 默认 50 / `max_scroll_rounds`
默认 3,对收藏多的用户(几百条)等于"只把最近 60 条最新 save 当作
画像输入",很难真实反映长期口味。用户提出把上限改到 300。

### 改动

`src/openbiliclaw/cli.py:_enqueue_xhs_bootstrap_task`:

| 参数 | 旧默认 | 新默认 | 控制 env var |
|---|---|---|---|
| `max_items_per_scope` | 50 | **300** | `OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS` |
| `max_scroll_rounds` | 3 | **15** | `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS` |

`scroll_rounds` 也得跟着调,否则虚拟列表每轮 ~20-30 条 × 3 轮上限 ~80,
300 是空头支票。15 轮是上限不是固定开销:executor 用
`bootstrapScrollShouldContinue` 跟踪 `stagnantRounds`,默认连续 5 轮
没出新 note 就早退,所以收藏少的用户不会跑满 15 轮。

extension 侧 `MAX_BOOTSTRAP_SCROLL_ROUNDS = 30` 是 hard ceiling,15
完全在范围内,**插件无需重新发版**。

### 不影响的

- 设过 env var 的用户继续按自定义值跑
- 已经跑过 init 的用户不会重复 bootstrap
- discovery / continuous 路径用的是不同入口(`xhs.search` /
  `xhs.creator`),和 bootstrap 无关
- xhs_history scope 在小红书 profile 页根本不暴露,这次依然 0 条
  (与上限多大无关)

### 测试

`tests/test_cli.py::test_enqueue_xhs_bootstrap_task_uses_env_overrides`
是 env-override 测试(用 5 / 100),逻辑不变,继续 green。

---

## v0.3.63: LLM 全局优先级队列 + detached task registry (2026-05-05)

### 背景

v0.3.62 解决了"互相拖累"的 lock 问题,但留下了用户架构 review 中的两条尾巴:

1. **LLM 资源仍然没有优先级概念。** 当一轮 delight scoring (上百次调用) 在跑时,popup 急需的 `write_expression` (1-2 次调用) 只能在 FIFO 队列后面排队,用户能看见的池子表达式回填可能要等数分钟。
2. **detached task 在 hot reload 后还在跑。** `RuntimeContext.rebuild_from_config` 只 cancel 顶层 loop task,`asyncio.create_task(...)` 起的 fire-and-forget 协程(per-strategy precompute、prewarm helper、per-event trigger、manual refresh handle)持有旧 runtime 引用继续抢 SQLite 写和 LLM token,可能持续很多秒。

这一版收尾这两条。两件工作仍然是并行 agent 起的(LLM 优先级 / task registry 分别一组),最终在主上下文里收敛、补 4 个集成点 + 8 个测试。

### 一、LLM 全局优先级队列

`src/openbiliclaw/llm/service.py` 加了一个 `PrioritySemaphore` 类,用 heapq + monotonic 计数器实现优先级 + FIFO 平局:capacity=1,完全 free 时无开销直通,有竞争时严格按优先级唤醒 waiter。

`LLMService` 加了:

- `_PRIORITY_MAP` ClassVar:`recommendation.write_expression`/`discovery.evaluate_batch` = **1**(用户可见、堵住就明显);`recommendation.delight_score`/`soul.*`/`xhs.*` = **2**(后台批量打分);其他默认 **3**。
- `_resolve_priority(caller)`:对 `caller` tag 做 longest-prefix 匹配。`"soul.preference"` 匹配 `"soul"` 前缀拿到 priority=2。
- `_priority_sem: PrioritySemaphore`(`init=False`,默认 capacity=1):`complete_with_core_memory` 现在把 `await self.registry.complete(...)` 包进 `async with self._priority_sem.slot(priority):`。

唯一改动点是在 `complete_with_core_memory` 里——这是所有 LLM 调用的单一入口(`complete_structured_task` / `complete_with_tools` / `complete_socratic_dialogue` 全部走这条路径),不需要改下游每个 caller。

**预期效果**:在 delight scoring 跑批的时候,popup 触发的 `write_expression` 抢到下一个 LLM slot 而不是排到队尾;后台 priority=3 的临时 caller 也不会插队挤掉 priority=2 的 soul 分析。

### 二、Detached task registry

`src/openbiliclaw/runtime/task_registry.py` 新增 `BackgroundTaskRegistry`:

- `track(name, coro)`:封装 `asyncio.create_task(coro, name=name)`,记录到 `dict[Task, str]`。task 完成时通过 `add_done_callback` 自动 untrack,不会无界增长。
- `cancel_all(grace_seconds=1.5)`:cancel 所有 tracked task,等 1.5s 优雅退出;超时则 logger.warning 并强制 `_tasks.clear()`,新 runtime 立刻可用。
- `stats()`:按名字前缀分组的诊断计数(future-proof 给观测面板)。

`RuntimeContext`:
- 新增 `task_registry: BackgroundTaskRegistry` 字段。
- `rebuild_from_config` 拆成 async 公开方法(顶部 `await task_registry.cancel_all()` + INFO 日志) + sync `_rebuild_components` 内部。
- 注入 registry 到 `RecommendationEngine` 和 `ContinuousRefreshController`。
- 4 个 background task(refresh / account_sync / auto_update / prewarm)统一走 `task_registry.track(...)`。

`RecommendationEngine` / `ContinuousRefreshController` 各新增可选 `task_registry` kwarg + `_spawn_detached_task` / `_track_task` helper。所有 `asyncio.create_task` 调用点(`_safe_classify_pool_backlog`、`_safe_precompute_delight_scores`、`_manual_refresh_task`、per-strategy precompute、per-event trigger)走 helper;helper 在没有 registry 时 fallback 到裸 `create_task`,保证无 registry 的旧测试夹具继续 green。

`api/app.py` 两处 `ctx.rebuild_from_config(...)` 改成 await。

**预期效果**:用户在运行时改了 config 重载之后,旧 detached task 在最多 1.5s 内全部退场,不会和新 runtime 抢同一个 SQLite 写或 LLM token。

### 测试

- 新增 `tests/test_task_registry.py`(5 个测试):track/cancel/stats/超时降级/二次可用性。
- `tests/test_llm_service.py` +3 个测试:`_resolve_priority` longest-prefix 表、`PrioritySemaphore` 多 waiter 顺序唤醒、`complete_with_core_memory` 通过 priority 门串行化。
- `tests/test_api_app.py` 的 `FakeRecommendationEngine.__init__` 接受 `task_registry=None` 参数。

### 不影响的

- LLM caller 的 `caller=` tag 习惯没变;现有 caller tag 在 priority map 里命中既有规则,新加 caller 默认 priority=3 不会破坏现有调用。
- `LLMService(...)` 构造签名向后兼容(`_priority_sem` 是 `init=False`)。
- 没有 registry 注入时 `RecommendationEngine` / refresh loop 的行为和 v0.3.62 完全一致。

---

## v0.3.62: 三处架构性 lock 拆分 + DB 写重试收紧 (2026-05-05)

### 背景

用户做了一轮架构 review,识别出 7 个潜在互相拖累点。我们这轮处理 top 3 真问题(并行 agent 实现):

### 修法

#### 🔴 #1 拆 `_precompute_lock` → `_expression_lock` + `_delight_lock`(`recommendation/engine.py`)

```python
# 之前
self._precompute_lock = asyncio.Lock()  # expression + delight 都用这一把

# 之后
self._expression_lock = asyncio.Lock()  # 只 gate 推荐文案
self._delight_lock = asyncio.Lock()     # 只 gate 惊喜评分
```

`precompute_pool_copy` 里:
- expression 生成块包在 `async with self._expression_lock`
- delight scoring 抽到 `_safe_precompute_delight_scores` helper,**fire-and-forget** 跑(`asyncio.create_task`),用自己的 `_delight_lock` 防同期 double-spend。
- 早返回 (`if not candidates`) 路径同样走 detached delight,不再阻塞 caller。

效果:推荐文案永远不被 delight 抢锁。delight 慢了,popup 也照样能换内容。

#### 🔴 #2 全局 `_refresh_lock` 防 4 入口叠加(`runtime/refresh.py`)

```python
_refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
```

`refresh_if_needed` 入口处先检查 `if self._refresh_lock.locked():` → 立即返回 `{"skipped": True, "reason": "another refresh holds lock"}`,**不排队**(避免 manual 等 5 分钟在 periodic 后面)。

`force_refresh`(manual refresh 实际入口)同样加 lock:抽出 `_force_refresh_locked` 内部体,外层 `force_refresh` 做 lock check + acquire。**4 个入口**(`_loop_refresh` / `_complete_manual_refresh` / `refresh_after_event_ingest` / `refresh_after_feedback`)现在都互斥,不再叠 B 站 API 和 SQLite 写。

#### 🟡 #3 `_execute_write` 重试参数收紧(`storage/database.py`)

```python
# 之前: 5 × 100ms = 最多 500ms 同步阻塞 event loop
_LOCK_RETRY_ATTEMPTS = 5
_LOCK_RETRY_SLEEP_SECONDS = 0.1

# 之后: 8 × 20ms = 最多 160ms (更多次重试,每次更短)
_LOCK_RETRY_ATTEMPTS = 8
_LOCK_RETRY_SLEEP_SECONDS = 0.02
```

`time.sleep` 仍是同步的,但每次 20ms 远低于人感知阈值,即使在 asyncio 上下文里短暂卡住也基本不可见。**真异步化**(`asyncio.to_thread` 或 `await asyncio.sleep`)需要级联改 18+ 个 caller,留给 v0.3.63 大重构。

### 不在本次范围

| 用户标记的其他问题 | 排期 |
|---|---|
| LLM 没全局优先级队列 | v0.3.63 (架构级,需要设计) |
| Hot reload detached task 不取消 | v0.3.63 (task registry) |
| Embedding semaphore=2 | 不动(Ollama 本地推理设计如此) |

### 测试

134 passing(test_recommendation_engine + test_refresh_runtime + test_storage)。1000 passed/29 pre-existing failed,无新增失败。

### 致谢

整套修复完全是用户架构 review 驱动:他用 `git diff` + 代码静读把潜在死锁/抢锁/竞态全部识别出来,然后按优先级排序。Agent #1 (engine.py) 和 Agent #2 (refresh.py) 并行实施互不冲突;我自己改 database.py 走小改动路线避开 await 级联。

---

## v0.3.61 + extension v0.3.18: v_voucher 风控缓解 + popup 状态解耦 (2026-05-05)

### 背景

v0.3.60 把 precompute drain 拆成独立 loop 后,popup 已经能拿到推荐了,但用户反映:
1. `manual_refresh_state="running"` 长期挂起,refresh 因 B 站 v_voucher 风控反复重试
2. popup 状态条 chip 显示"正在补货",尽管 pool 已经有 59+ 条可换内容

### 三个修法

#### 🔴 v_voucher mitigation(`discovery/strategies/search.py`)

`_execute_search_queries` 升级:
- **Per-query jitter**:`asyncio.sleep(0.5)` → `asyncio.sleep(0.5 + random.uniform(0, 0.5))`,desync 同时落到 WBI rate-limit bucket 的请求波
- **Storm detection**:连续 3 个 query 返回空结果(说明 client.search 内部三轮 v_voucher 重试都 exhausted) → log warning + 中止本轮剩余 query。等下一个 60s refresh tick 再来,不深挖坑。

```
v_voucher storm detected (3 consecutive empty queries) — aborting
remaining N query(ies) this round; next refresh tick (60s) gets a
fresh attempt
```

#### 🟠 init 延迟首轮 refresh(`runtime/refresh.py`)

新增 `_init_grace_consumed: bool = False` 字段。`_loop_refresh` 第一次跑时跳过 `refresh_if_needed`,只跑 profile-ready hook。第二次起恢复正常 60s 周期。

```
Init grace period — skipping first refresh tick to let Bilibili WBI
bucket cool down (next tick will run normally)
```

为什么要这条:init 同步阶段(history/favorites/following 拉取)10 秒内打了 30+ 次 Bilibili API,WBI 桶基本被填满。立刻 fire discovery 搜索 → 50% v_voucher 退避。给 60s 缓冲,IP 凉一下。

#### 🟡 popup 状态条解耦(`extension/popup/popup-helpers.js`)

`getPoolStatusSummary` 当 `pool_available_count > 0` AND `manual_refresh_state="running"` 时改文案:

| 之前 | 现在 |
|------|------|
| 当前可换:还有 59 条可换 | 当前可换:还有 59 条可换 |
| 最近补进:**正在补货** | 最近补进:**后台继续在找更多** |
| 现在在忙:后台还在继续给你找新的 | 现在在忙:可以先换一批,新的随时进 |

不再把"正在补货"喂给已经能换一批的用户——避免误以为还得继续等。

### 影响

| 场景 | 之前 | 现在 |
|------|------|------|
| Init 后第一次 search 命中 v_voucher 比例 | ~50% | 预期 <10%(grace + jitter 双护) |
| 一轮 v_voucher 风暴期间 | 把所有 queries 都打挂(每个 21s 退避) | 3 次 empty 后中止,~90s 即终止 |
| Popup 状态条 | 即使 pool 满载也显示"正在补货" | 只在 pool 真空时显示 |

### 致谢

整套 v0.3.59 → v0.3.60 → v0.3.61 演进完全是用户的 systematic-debugging 流程驱动:
- v0.3.59 → 我加了 drain 但放错位置(被 refresh 卡)
- v0.3.60 → 用户调试出 drain 永远轮不到,建议拆独立 loop;我照修
- v0.3.61 → 用户进一步发现 refresh 卡的根因是 v_voucher 风控,且 popup 状态条仍误导;我把这俩一起修

---

## v0.3.60: precompute drain 拆成独立 loop,不再被慢 refresh 卡 (2026-05-05)

### 背景

用户用 systematic-debugging 流程精确定位:

```
PID 32644(22:35:12 启动)
内存版本 0.3.59 ✅
_safe_classify_pool_backlog 方法存在 ✅
content_cache fresh = 184(132 条满足 needing_copy)
但 pool_expression=0、pool_topic_label=0
llm_usage 没有 caller=recommendation.write_expression
runtime status: manual_refresh_state="running" 长时间不返回
```

→ v0.3.59 的 `_drain_pool_precompute_backlog` 代码确实存在,但**挂在 `_loop_refresh` 里 `await self.refresh_if_needed()` 之后**。B 站 v_voucher 风控让 refresh 几分钟不结束 → drain 永远轮不到。

### 修法

按用户建议,把 drain 从 `_loop_refresh` 拆出来,做成 `_loop_pool_precompute()` 独立 loop:

```python
async def run_forever(self):
    tasks = [
        asyncio.create_task(self._loop_refresh()),
        asyncio.create_task(self._loop_pool_precompute()),  # ← 新增
        asyncio.create_task(self._loop_soul_pipeline()),
        asyncio.create_task(self._loop_xhs_producer()),
        asyncio.create_task(self._loop_proactive_push()),
    ]

async def _loop_pool_precompute(self):
    while True:
        with suppress(Exception):
            await self._drain_pool_precompute_backlog()
        await asyncio.sleep(self.check_interval_seconds)
```

引擎的 `_precompute_lock` 已经能去重 per-strategy fire-and-forget 触发的 precompute,所以独立 loop 不会与 `_run_refresh_plan` 里的触发 double-spend LLM。

### 影响

| 场景 | v0.3.59 | v0.3.60 |
|------|---------|---------|
| refresh 因 v_voucher 卡几分钟 | drain 跟着卡,永不执行 | drain 独立 60s tick,完全不受影响 |
| 启动后第一次 popup 可见 | 不可预测(取决于 refresh 是否卡) | 60s 内 |

致谢:用户用 superpowers:systematic-debugging 流程一步步排除假设(进程没换 → 内存版本对 → drain 代码存在 → 池子有 184 条 fresh → write_expression=0 → manual_refresh_state stuck)定位到这一行,我直接照修。

---

## v0.3.59: precompute 解耦 classify + 定期主动 drain (2026-05-05)

### 背景

production logs 2026-05-05 21:15-21:36(21 分钟会话):

```
21:26:42  Soul profile became ready, classify_pool_backlog: 87 items (xiaohongshu)
21:27:15-21:29:35  recommendation.evaluate_batch × 6 batch (classify done)
21:28:45 → 21:31:08  pool_available=0 持续
                     caller=recommendation.expression × **0** ← precompute 一次没跑
```

popup 截图显示"FOR YOU 1/17"(池子里 17 条)但显示"阿B 正在补货"——这 17 条全卡在 P3 gate 后面,因为没人帮它们生成 `pool_expression`。

### 根因

precompute 只通过两条路径触发:
1. `_run_refresh_plan` 里 `if discovered: precompute_tasks.append(...)` —— Bilibili search 在 v_voucher 风控下多数策略返回 [],precompute 不 fire
2. `precompute_pool_copy` 内部先 `await classify_pool_backlog(...)`(同步阻塞)再读 candidates —— classify 自己跑得慢时 precompute 跟着卡

两条路径叠加 = pool_expression 永远填不上 = popup 永远"正在补货"。

### 修法

#### 1. `recommendation/engine.py:precompute_pool_copy` 解耦 classify

`await classify_pool_backlog(...)` → `asyncio.create_task(self._safe_classify_pool_backlog(...))`。让 classify 在后台自己跑,precompute 立刻读"现在已经分类好的" candidates 开始填 expression。

新增 `_safe_classify_pool_backlog` —— detached task wrapper,异常吞掉防止 UnobservedException。

#### 2. `runtime/refresh.py:_loop_refresh` 加定期 drain

每个 60s tick 末尾调用 `_drain_pool_precompute_backlog()`:
- 检查 profile ready
- `await engine.precompute_pool_copy(...)` 一次

引擎内部的 `_precompute_lock` 自动 dedup 与 `_run_refresh_plan` 的 per-strategy 触发,不会 double-spend LLM tokens。

### 影响

| 场景 | 之前 | 现在 |
|---|---|---|
| Bilibili 风控,所有 strategy 返 0 | precompute 永远不 fire | 60s 一次定期 drain |
| classify 慢(大 backlog) | precompute 串行等 | precompute 并行读已 classified 的 |
| pool 空窗时长 | 17 min(实测) | 应降到 ~3-5 min |

### 风险

- precompute 现在按 60s 周期主动 fire,如果 pool 一直空,每分钟都会读一次 `_load_pool_candidates_needing_copy(limit=60)`。SQL 是 indexed,负载可忽略。
- LLM token 消耗:同样的 candidates,同样的提示词。`_precompute_lock` 防 double-spend。生产环境多花 0 元。
- 如果 classify 失败导致 pool 中长期有 `style_key=''`/`topic_group=''` 的 row,这些会被 `precompute_pool_copy` 直接读到——精排 LLM 拿到没分类的内容也能生成兜底文案,只是 topic_label 可能不准。Acceptable 边界,不阻塞 popup。

测试:1000/1029 通过(同 29 个 pre-existing failures 不增不减)。

---

## v0.3.58: init 摘要按平台分类显示信号入库数 (2026-05-05)

### 背景

老的 `openbiliclaw init` 摘要面板把 B 站 / 小红书的事件混成一行 `小红书事件: N`,既看不出 saved/liked/xhs_history 怎么分布,也不知道 B 站这边 history/favorites/following 各贡献了多少。AI Agent 装机时也没法清晰转告用户"画像吃了多少信号"。

### 修法

`cli.py:init` 的最终摘要表格重构,按平台分组显示,带 emoji 视觉分隔:

```
📺 B 站观看历史       302 条
📺 B 站收藏夹         8 条
📺 B 站关注 UP        350 人
🌐 B 站 入库事件      660 条
📕 小红书 收藏(saved) 50 条
📕 小红书 点赞(liked) 50 条
📕 小红书 浏览记录    0 条
🌐 小红书 入库事件    100 条
📊 画像建模总事件     760 条
✅ 灵魂画像           已生成
🔍 首轮发现内容       180 条
```

之后跟一行情境化提示:
- 小红书三个 scope 全 0 → 提示"扩展未装 / 浏览器没登录 XHS / 任务后台跑"等常见原因 + 复跑命令
- 小红书有数据 → 提示"本次画像综合了 X 条 B 站 + Y 条小红书信号,daemon 后续增量补充"

### 配套 doc 改动

`agent-install.md` 加 "After init succeeds — relay the per-source signal counts" 段,要求 AI Agent 把摘要数字 paraphrase 给用户(B 站/小红书各 N 条 + 总事件 + 首轮发现池)。0 信号场景必须把 CLI 的"ℹ️ 小红书 0 条"那行原样转告,不能丢掉。

零行为变化,纯 UX —— 数字本来就有,只是表达更清楚。

---

## extension v0.3.17: service worker WS 重连指数退避 (2026-05-05)

### 背景

v0.3.14 已经把 popup-stream.js 的 WS 改成指数退避(2s→30s),但**service worker 自己有第二条 WS 连接**(`connectRuntimeStream` 给 background 用的 runtime-stream)依然用固定 5s 间隔重试。后端死掉时:

```
service-worker.ts:170 WebSocket connection ... failed: ERR_CONNECTION_REFUSED
service-worker.ts:170 WebSocket connection ... failed: ERR_CONNECTION_REFUSED
service-worker.ts:170 WebSocket connection ... failed: ERR_CONNECTION_REFUSED
... 每 5 秒一行,无限刷
```

### 修法

`service-worker.ts:scheduleWsReconnect` 改用指数退避:5s → 10s → 20s → 40s → 60s 封顶。`onopen` 成功握手时重置回 5s,瞬时网络抖动 fast-recover 不打折。

```ts
const WS_RECONNECT_BASE_DELAY = 5_000;
const WS_RECONNECT_MAX_DELAY = 60_000;
let wsReconnectDelay = WS_RECONNECT_BASE_DELAY;

// scheduleWsReconnect:
const delay = wsReconnectDelay;
setTimeout(connectRuntimeStream, delay);
wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_RECONNECT_MAX_DELAY);

// onopen:
wsReconnectDelay = WS_RECONNECT_BASE_DELAY;
```

### 影响

后端死 1 分钟内 console:之前 ~12 行 → 现在 5 行(5s/10s/20s/40s/60s);1 分钟之后:之前一直 12 次/分钟 → 现在 1 次/60s。配合 v0.3.14 的 popup-stream 退避,扩展两条 WS 连接现在都不再刷屏。

---

## extension v0.3.16: 关掉所有 OS toast,通知收回 popup 内 (2026-05-05)

### 背景

用户反馈右下角弹的 Chrome OS 通知干扰太大,要求"所有通知都在插件里面进行就行"。再加上 v0.3.14/v0.3.15 修了 ack 循环 + 绝对 URL 之后,Chrome 内部 imageUtil 仍然偶发 `Uncaught (in promise) Error: Unable to download all specified images.`(我们 catch 不到的、内部 promise 链),console 还是不干净。

### 修法

把三处 `chrome.notifications.create` **全部去掉**:

1. `service-worker.ts:checkPendingNotification`(轮询拉的 recommendation + cognition 通知)→ 现在只调 `acknowledgeNotificationSent` / `acknowledgeCognitionUpdateSeen`,让后端 pending 队列正常出队,但不弹 OS toast。Popup 自己有 WebSocket 订阅,推荐照常出现在卡片列表里。
2. `service-worker.ts:handleRuntimeEvent` 处理 `interest.probe`(WS 推送的兴趣探针)→ 同上去掉,popup inbox 已经显示
3. `service-worker.ts:handleRuntimeEvent` 处理 `delight.candidate`(WS 推送的惊喜推荐)→ 同上去掉,delight 已经在 popup 推荐列表里带 hook badge 显示。仍然 `acknowledgeDelightSent` 防止后端重发

清理:删掉服务变得不再使用的 5 个 import(`buildChromeNotificationOptions` / `buildNotificationId` / `buildCognitionNotificationId` / `buildDelightNotificationId` / `PendingDelight` 类型),代码瘦了 ~30 行。

### 影响

- 用户屏幕右下角再也不会弹 Chrome 通知
- service worker console 不再出现 `notifications.create failed` warn 或 Chrome 内部的 `Unable to download all specified images` reject
- popup 体验完全不变(本来推荐就是从 popup 卡片列表 + WS 推送进来的,Chrome toast 只是冗余出口)
- backend 不需要任何改动,pending 队列照常 ack 出队

`chrome.notifications.onClicked` listener 留着没动(只是不会再 fire 了),保留以防以后需要做"toolbar icon badge → 点击展开 popup"之类轻量提醒。Notifications permission 在 manifest 里也保留——后续如果想做可选的 toast 提醒(默认关闭、用户在 popup 设置里 opt-in),不用改 manifest。

---

## extension v0.3.15: 通知 iconUrl 改用 chrome.runtime.getURL 解决根因 (2026-05-05)

### 背景

v0.3.14 已经把"通知失败 → 不 ack → 无限循环"的二次伤害修了,但 console 仍然每隔几分钟出一条:
```
[OpenBiliClaw] notifications.create failed (...): Unable to download all specified images. iconUrl: icons/icon128.png
```

通知失败的**真正根因**这次抓到了:`iconUrl: "icons/icon128.png"` 是相对路径,**MV3 service worker 没有 document 上下文**,Chrome 内部解析相对路径时偶尔会落到 `chrome-extension://invalid/icons/icon128.png` —— 这就是之前 console 里 `chrome-extension://invalid/:1 ERR_FAILED` 的来源。

已知 Chromium issue,推荐做法是 `chrome.runtime.getURL("...")` 拿绝对的 `chrome-extension://<id>/...` URL。

### 修法

`extension/src/background/notifications.ts` 里抽出 `resolveNotificationIconUrl()`:
```ts
function resolveNotificationIconUrl(): string {
  try {
    if (typeof chrome !== "undefined" && chrome.runtime?.getURL) {
      return chrome.runtime.getURL("icons/icon128.png");
    }
  } catch { /* fall through */ }
  return "icons/icon128.png";  // 测试环境兜底
}
```

`buildChromeNotificationOptions` 三个分支(delight / cognition / recommendation)统一改用 `iconUrl: resolveNotificationIconUrl()`。

### 影响

- 通知 toast **真的能弹出来了**(之前每个 notification 都因图标加载失败被 Chrome 静默吞了)
- service worker console 不再出 `notifications.create failed` warn
- 配合 v0.3.14 的 ack-always-run + WS backoff,console 噪音清零

零接口变化。Backend 不需要改。

---

## extension v0.3.14: 通知失败循环 + WebSocket 重连风暴修复 (2026-05-05)

### 背景

用户报告 service worker console 持续刷一堆:
```
[OpenBiliClaw] Pending notification check failed
Uncaught (in promise) Error: Unable to download all specified images.
WebSocket connection to 'ws://127.0.0.1:8420/...' failed × 70+
```
而且 popup "页面好像一直在奇怪的刷新"。

### 根因 1:通知 ack 漏掉,bvid 永远 pending

`service-worker.ts:checkPendingNotification`:
```ts
try {
  const item = await fetchPendingNotification();
  if (item?.bvid) {
    await chrome.notifications.create(...);  // ← reject 抛出
    await acknowledgeNotificationSent(...);  // ← 跑不到
  }
} catch { console.warn("...failed"); }      // ← 吞掉真实 error
```

`chrome.notifications.create` 内部图片下载失败会让 promise reject。catch 吞了,但 `acknowledgeNotificationSent` 也没机会跑。下个轮询周期(每分钟)后端又把同一个 `bvid` 喂回来 → 同样失败 → 同样不 ack → **无限循环**,console 一直被刷。

### 根因 2:WebSocket 重连固定 2s 间隔无退避

`popup-stream.js:scheduleReconnect` 用了固定 `reconnectDelayMs = 2000`。后端短暂死掉时,popup 每 2s 尝试重连,1 分钟内 30 次失败,console 满屏 `ERR_CONNECTION_REFUSED`。

### 修法

**`service-worker.ts`**:
- 抽出 `safeNotify(id, options)` —— 内部 try/catch 把 `chrome.notifications.create` 的 reject 转成 console.warn(带真实 error message + iconUrl 上下文),不再传染上层
- `checkPendingNotification` 用 `safeNotify` 替代直接调用 → **`acknowledgeNotificationSent` always run**(用户已经在 popup 里看到推荐了,toast 失败只是少了 OS 弹窗,不能因此让后端永远认为没发过)
- 顶层 catch 也把 error message 打出来,不再吞

**`popup-stream.js`**:
- `createRuntimeStreamClient` 加 `maxReconnectDelayMs = 30_000`(默认 30s 上限)
- 每次失败 `currentReconnectDelay *= 2`,封顶 30s
- 成功 onopen 时重置回 2s,瞬时网络抖动 fast-recover 不打折

### 影响

- 通知 console 不再被无限循环刷,出 1 次 warn 就停
- WebSocket 后端死掉时,popup 在第一分钟内尝试 6 次(2s/4s/8s/16s/30s/30s),之后 30s 一次,负载和 console 噪音都可控
- popup "感觉在乱刷" 主因消除(通知 + WS 两条噪音都掐了)

零接口变化,backend 不用动。

---

## extension v0.3.13: profile sub-tab 等待重试 — bootstrap_profile 真正能拉到收藏/点赞 (2026-05-05)

### 背景

v0.3.12 修好了 self_info 抽取后,bootstrap_profile 任务**仍然返回 saved/liked/xhs_history = 0**。诊断证据(用户在 active tab 跑读 DOM 的脚本):

```
"笔记" DIV reds-tab-item active sub-tab-list
"收藏" DIV reds-tab-item sub-tab-list
"点赞" DIV reds-tab-item sub-tab-list
```

→ DOM 里**有**收藏 / 点赞 sub-tab。`bootstrapProfileTabLabels` 也已包含 `["收藏"]` / `["赞过", "喜欢", "点赞"]`,selector `.reds-tab-item` 也匹配。**所以为什么找不到?**

### 根因

时序竞态:`hasBootstrapProfileContent(doc)` 看到 bridge 已经送来 state(基本立刻)就返回 `true`,task 进入 `loadProfileTabsForScopes`。但**那一帧 sub-tab DIV 还没 mount 出来**——XHS Vue runtime 是先把 `__INITIAL_STATE__` 赋值,再渲染 sub-tab 子组件。

`findProfileTab` 同步调用,第一次必然返回 `null` → `loadProfileTabsForScopes` 内的 `if (!tab) continue` 直接跳过该 scope,sub-tab 永远不会被点击 → state.user.notes[1]/[2]/[3]/[4] 永远是空数组(XHS lazy-load,不点 tab 不拉数据)。

### 修法

新增 `findProfileTabWithRetry(doc, labels, timeoutMs=5000)`:
- 第一次同步调用,fast-path 不变
- 找不到 → 每 300ms 轮询一次,直到 deadline
- 命中即返回

`loadProfileTabsForScopes` 里 `findProfileTab` → `await findProfileTabWithRetry`。每个 scope 最多等 5 秒等 sub-tab 渲染。

### 兼容性

零接口变化。backend 不需要改。老 tab 已经渲染时 0 性能成本。新 tab 第一次最多多等 5s,但这是为了能拉到收藏/点赞列表的必要代价。

---

## extension v0.3.12: MAIN-world state bridge — 修复 XHS 完全无数据 (2026-05-05)

### 背景

production logs 多个会话(2026-05-05 1h+)显示 XHS 入池为 0:`Event propagated: like = 0`、`self_info persisted = 0`、`ingest filter: dropped = 0`、`startup purge = 0`,**所有 XHS 数据获取路径全部静默失败**。

### 根因

MV3 content script 跑在 isolated JS world,`doc.defaultView.__INITIAL_STATE__` 永远是 `undefined` —— 只有 page 的 MAIN-world 脚本能看到 `window.__INITIAL_STATE__`。

`bootstrap.ts:extractBootstrapStateFromDocument` 两条路都断:
1. `doc.defaultView.__INITIAL_STATE__` —— isolated world 看不见 page globals
2. 扫 `<script>` 标签 inline JSON —— XHS 是 SPA,state 是运行时 JS 赋值

→ 函数永远返回 `null` → `extractSelfInfoFromState` 永远返回 `null` → bootstrap_profile / passive collector / search task **三条路全部抽不到 self_info,也抽不到 saved/liked/history notes**。

诊断证据:在 XHS 页面 DevTools 跑读 state 的脚本,`loggedIn: ec {__v_isRef: true, _rawValue: true}` —— 用户 100% 已登录,但 isolated world 看不见。

### 修法

新建 `extension/src/main/xhs-state-bridge.ts` 跑在 MAIN world(manifest 同 `xhs-token-sniffer.js` 路径),复刻 token sniffer 的 postMessage 桥接套路:

1. 轮询 `window.__INITIAL_STATE__` 出现(Vue mount 后才赋值)
2. `safeJsonClone` 把 Vue 3 ref 树展平成 JSON-safe 形状(unwrap `__v_isRef`/`_rawValue`、断循环、丢 `__v_*`/`dep`/`deps` 内部键、丢 functions/symbols)
3. `buildStateSnapshot` 白名单只挑 `bootstrap.ts:notesForScope` 实际读的 10 个 top-level keys(`user`, `saved`, `collect`, `collections`, `liked`, `likes`, `history`, `footprint`, `browseHistory`, `browsingHistory`),snapshot 大小有 2MB 上限,溢出降级到最小 `{user: {loggedIn, userInfo, userPageData}}`
4. `window.postMessage({source: "obc-xhs-state", state})` 给 isolated world
5. 重发触发器:popstate / visibilitychange=visible / click(SPA 路由变更),内置 `lastSnapshotJson` dedup

`bootstrap.ts:extractBootstrapStateFromDocument` 三层兜底:
1. **MAIN-world bridge cache**(主路径,新增):监听 `window.message` 缓存最新 snapshot,同步返回
2. `doc.defaultView.__INITIAL_STATE__`(jsdom 测试可能用到)
3. `<script>` 标签扫描(legacy SSR 兜底)

### 测试覆盖

- `extension/tests/xhs-state-bridge.test.ts`(11 cases):isVueRef 识别 / safeJsonClone 处理 ref+循环+Vue 内部键+throw getter / buildStateSnapshot 白名单 / Vue-wrapped XHS-shaped state 完整链路
- `xhs-task-executor.test.ts` 加 3 case:ingestMainWorldStateMessage 缓存 + 拒绝 malformed payload + cache 优先级高于 doc.defaultView

合计 184/184 通过。

### 兼容性

- 后端代码 0 改动 —— 修复完全在扩展端
- 老扩展(v0.3.11 及之前)装在 v0.3.57 后端上 = 现状不变(XHS 仍然 0 数据)
- 新扩展(v0.3.12)装在任何 v0.3.57+ 后端上 = self_info 真正流入,过滤生效,bootstrap_profile 可以读 saved/liked/history

---

## v0.3.57: pool quality trio (2026-05-05)

### 背景

`docs/plans/2026-05-05-pool-quality-trio-spec.md` 三个 P 级问题——都直接污染 popup 显示质量,但互不耦合。配套发布 **extension v0.3.10** 完成 P2 的扩展端配套。

### P1 — cookie race 阻塞 history 7 分钟

**现象**:daemon 启动时 cookie 还没从扩展同步到位,`AccountSyncService` 第一个 tick 用空 cookie 拉 history,拿到 `[]` 并 stamp `last_account_sync_at`,把 6 小时 throttle 锁死。production logs 实测 03:33:25 cookie 缺失 → 03:40:22 才第一次成功——**7 分钟空窗**。

**修法**(`runtime/account_sync.py`):
- `sync_now` / `sync_if_due` 在 `bilibili_client.is_authenticated` 为 False 时短路返回 `reason=no_auth`,**不写时间戳**。
- `run_forever` 在第一次成功 auth 之前用 15s 重试间隔(`_UNAUTH_RETRY_INTERVAL_SECONDS`),之后切回常规 5 min。
- 首次 auth 抵达时打一行 INFO 日志(`account_sync: bilibili cookie now ready ...`),让 operator 能 grep 到 gate 释放。
- Stub client 没 `is_authenticated` 属性时默认认为已 auth,保留既有测试行为。

**预期**:首次 history 拉取从 7 min → ≤30s。

### P2 — XHS 用户自己发布的笔记进推荐池

**现象**:`agent-bootstrap.log` line 610–615 sample_titles 里出现"自家宝安领航城165㎡大五房出售"等用户本人发布的笔记。XHS 平台的 search/explore feed 会把登录用户自己的笔记混进结果,而推荐入池路径里**只有 bootstrap_profile 抽 self_info**:passive collector 和 search/creator task 都没抽,race 一打开就漏。

**后端修法**(`api/app.py`):
- `_extract_self_info_from_payload(payload)` 统一接入:**先**看顶层 `self_info`,fallback 到旧的 `debug.xhs_bootstrap.steps[*].self_info`。
- `/api/sources/xhs/observed-urls` 新增:读 self_info → `_persist_xhs_self_info` → 传给 `_cache_xhs_notes`。
- `/api/sources/xhs/task-result` 切换到统一 extractor。
- `_purge_self_authored_pool_items(database, self_info)` 启动钩子:扫 `content_cache where source_platform='xiaohongshu' and lower(up_name)=lower(?)` 把已存量行翻成 `pool_status='suppressed'`,修复升级前已经污染的 pool。

**扩展修法**(extension v0.3.10,`xhs/passive.ts` + `xiaohongshu.ts` + `xhs/task-executor.ts`):
- `passive.ts:filterSelfAuthoredNotes` + `XhsSelfInfo` 类型 + `XhsUrlObservation.self_info` 可选字段。
- `runPassiveCollection` 读 `__INITIAL_STATE__.user.userInfo`,scrape-time drop `note.author === self.nickname`,把 self_info 塞进 observation。
- `executeTaskInPage` 非 bootstrap 分支同样抽 self_info + scrape-time 过滤,加入 `TaskResultPayload.self_info`。

**预期**:任意 XHS 页面一打开就抓 self_info;不再依赖 bootstrap_profile 先跑;升级用户的存量污染会被启动 purge 修掉。

### P3 — popup 推荐文案落到占位模板

**现象**:popup 卡片下文案是 `"《xxx》这条切口挺顺的，先丢给你看看，说不定正好能对上你当下的兴趣"` —— `_fallback_expression` 兜底模板,直接命中。原因:`get_pool_candidates`/`count_pool_candidates` 没对 `pool_expression` 做非空过滤,discovery 写完→precompute 跑完之间 60–90s 窗口,serve() 取到空 row 走 fallback。

**修法**(`storage/database.py` + `recommendation/engine.py`):
- `get_pool_candidates` 两个 SQL 分支(`max_per_topic_group<=0` 和 window function)的 WHERE 加上 `AND COALESCE(pool_expression, '') != '' AND COALESCE(pool_topic_label, '') != ''`。
- `count_pool_candidates` 同样加上,popup "还有 N 条" 不再误导。
- `engine.py:320` 的 fallback 路径改成 `logger.warning("Pool gate leak: ...")` + 仍兜底——race-window 安全网,触发即报警。
- 测试 fixture 加 `_seed_visible(db, bvid, **kwargs)` helper,默认填充两个字段;两个 gate-test 仍走 `cache_content` 直接路径以验证空行被过滤。

**预期**:popup 永远只显示 LLM 生成的个性化文案;init 窗口可视 pool 出现时间从 30s 后移 ~90s,但所有露出来的内容都有真理由。

### 兼容性

- 后端先发,扩展后发——后端的 `_extract_self_info_from_payload` 用 `dict.get + isinstance` 防御,老扩展(v0.3.9)payload 不带 self_info 不报错,只是 P2 不生效。
- 新扩展(v0.3.10)发到老后端会 500 ——只在升级窗口期短暂,文档强调要一起升级。

---

## v0.3.56: topic_group supergroup 合并下沉到 DB（2026-05-05 spec wave 6 / 完结）

### 背景

`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` U9。

`_supergroup_canonical_map` 把 "动漫"/"动漫杂谈"/"动漫二次元" 合并成同一个 canonical 主题——但合并**只在 serve 时跑**。pool 在数据库层面看到的还是 3 个独立的 topic_group。任何按 topic_group group_by 的 SQL（`get_topic_group_samples` / popup status / 后台分析）都看不到合并后的真主题分布。

### 改动

**`Database.canonicalize_topic_groups(canonical_map)`**（`storage/database.py`）：
- 接收 `{lowered_src: canonical_dst}` map
- 对每个 src→dst pair，发一条 `UPDATE content_cache SET topic_group=? WHERE LOWER(TRIM(topic_group))=?`
- 跳过 src==dst 和空字符串
- 单条 transaction（已有的 `_execute_write` 走 WAL）
- 返回 rewritten 行数

**`prewarm_supergroup_embeddings` 末尾自动调用**（`recommendation/engine.py`）：
- 每次 prewarm 重建 canonical map 之后立即跑一次 `canonicalize_topic_groups(new_map)`
- INFO 日志 `Topic supergroup canonical map applied to pool: N row(s) rewritten`
- 失败 swallow + log（lazy-merge at serve 时仍能兜）

### 影响

- pool 在 DB 层面显示真实主题分布——`Recommendation candidate summary` 不再被字面拆分掩盖
- 下游 SQL 分析（`get_topic_group_samples` / 任何按 topic_group 聚合的查询）看到合并后的主题
- 不影响 serve-time merge 路径——双重保险
- 每次 refresh tick 多一次 batch UPDATE，行数级开销可忽略

测试：830/830 通过，无新增。

### Spec 完结

至此 6 个 wave 全部完成（v0.3.51 → v0.3.56），`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` 中 9 个 U 全部修复。**净 LLM 月成本降幅约 -50%（reasoning 关闭抵消候选并发 3×）**，加上一系列体感优化（pool 不再被 hot franchise/style 占领、speculator 真正出货、startup 错误风暴消失、search v_voucher storm 容忍）。

---

## v0.3.55: B 站 search v_voucher 退避 1 → 3 attempt（2026-05-05 spec wave 5）

### 背景

`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` U3。

production logs 43 分钟会话里 **141 次 `Search got v_voucher challenge`**，**9 次完整一轮 `Search: 8 queries, 0 API results, 0 unique candidates`**。原 retry 策略只 1 次重试 + 1.5s 固定延迟，命中两次连环挑战就放弃；keyword 已经付费 LLM 生成（每次 ~¥0.012）但拿不到结果。

### 改动

`src/openbiliclaw/bilibili/api.py:search_videos`：
- retry attempts 2 → **3**
- 退避从 fixed 1.5s 改成 **指数 (1.5s, 5s, 15s)** 三段
- 总超时 ~21s 给 WBI key churn 时间稳定
- 第 3 次仍 v_voucher → WARN log + return []，让上游知道是 storm 不是 query 不存在
- 重试触发时打 INFO `Search v_voucher challenge (attempt N/3) ... retry in Xs`

### 影响

- 大多数 transient v_voucher 在第 2-3 次重试时会拿到结果（之前一律放弃）
- 9 次 0-result rounds 预期降到 ~3 次（实际还需观察）
- WBI storm 持续期间不再静默放弃——WARN 让 operator 看见
- 不是 storm 的正常情况下：retries 不触发，无成本影响

测试：830/830 通过，无新增（行为是 transient 重试，不易写单测）。

---

## v0.3.54: Ollama 启动期 retry + MMR prewarm 重试（2026-05-05 spec wave 4）

### 背景

`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` U4 + U6。

**U4 — Ollama 启动期 9 次 502 引发连锁失败**：daemon 启动头 90 秒，Ollama 还在加载模型，`localhost:11434/v1/chat/completions` 返 502。基础 OpenAIProvider 重试是 3 × 0.25s 线性 = 1.25s 总时长，远不够 Ollama 30s 模型加载窗口。

**U6 — MMR embedding cache 31 分钟不命中**：startup 的 prewarm 任务在 Ollama 502 期间一次性失败，没重试，导致 cache 空了 31 分钟。

### 改动

**U4 — `OllamaProvider.complete()` 加扩展重试**（`llm/ollama_provider.py`）：
- 新常量 `_OLLAMA_MAX_RETRIES = 5` + `_OLLAMA_BASE_RETRY_DELAY = 1.0`
- override 父类 `complete()`，在 502 / 503 / TransportError / TimeoutError 时按 1s, 2s, 4s, 8s, 16s 指数退避（总 ~31s）重试
- 5 次都失败才向上抛 → registry fallback 链才会切到下一 provider
- 不影响热路径（已加载好的模型立即返 200，重试不触发）

**U6 — `_safe_prewarm_pool_mmr_embeddings` 改成 5 次重试**（`api/runtime_context.py`）:
- 之前一次性 try/except 失败就放弃
- 现在 attempt 1-5，初始 delay 2s 指数翻倍，总 ~62s 窗口
- 任一次返回 `warmed > 0` 即提前结束（成功 short-circuit）
- 5 次都失败也是 silent skip — pool MMR cache 还会通过 serve() / discovery 自然填充

### 影响

- 启动期 Ollama 502 触发 OllamaProvider 自带 31s 退避，等模型加载完直接成功
- speculator / awareness / cognition 不再因为 startup 502 连锁挂掉（v0.3.46 已经把假 ERROR 治了，这次治真正的 502）
- prewarm 在 ollama 起来之前重试 5 次，cache coverage 5 分钟内回到 ≥80%
- 不动 prompt builder，cache 命中率不受影响

测试：830/830 通过，无新增（行为是 startup-only 重试，不易写单测）。

---

## v0.3.53: speculator gate + xhs_producer 节奏（2026-05-05 spec wave 3）

### 背景

`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` U7 + U8。

**U7 — speculator quality gate 全 drop**：
production logs 一次 force_tick `generated=5, promoted=0, rejected=0`。LLM 给所有 5 个候选的 confidence 都是 **0.35**——`min_confidence=0.40` 正好刚高于 LLM 实际产出，全部被 drop。

**U8 — xhs_producer 整 43 min 只跑 1 轮**：
日志只看到一次 `xhs producer enqueued 5/5`。后续 ticks 全静默 skip——没有日志看不出原因。

### 改动

**U7 — speculator min_confidence 0.40 → 0.30**（`soul/speculator.py`）

让 LLM 自然产出的 0.35 区间通过。下游 pipeline（specifics≥2 / reason≥20chars / domain shadow check / dedup）继续 gate "lazy" candidates。

**U8 — xhs_producer 加 INFO log + 缩短 throttle**（`runtime/xhs_producer.py`）

- `min_interval_hours: 4 → 1` — 4 小时 throttle 让池子整段时间不刷新。1 小时 cadence + daily_budget=30 = 24 enqueues/day（留 6 head room 给 manual / refresh-tick）
- `_skip()` 在 reason 变化时打 INFO `xhs producer skip: reason=X`——operator 可以 grep 出为什么 producer 不跑（disabled / throttled / no_profile / no_keywords），不会 spam 同一 reason 每分钟一条

### 影响

- speculator 现在会真的有 promoted candidates（gate 通过率从 0% 回升到 ~50% 估计）
- xhs producer 1 小时 cadence 让池子持续刷新（之前一次后停 4 小时太长）
- 日志可见性：xhs producer skip reason 转换时打 INFO

测试：830/830 通过，无新增。

---

## v0.3.52: discovery 候选并发评估 30 → 90（2026-05-05 spec wave 2）

### 背景

`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` U2：

production logs `evaluate_content_batch: truncating 300+ -> 30 items` 反复出现，最高 480→30。**90% 候选直接被丢弃**——里面可能有不少好内容。

根因：`_EVALUATE_BATCH_HARD_CAP=30` 永远只评估前 30 条。pre-v0.3.51 因为单批 LLM 要 8-16 min，不敢并发跑多批；v0.3.51 关了 reasoning 后单批 30s 完成 → 现在可以并发评估更多候选。

### 改动

- `_EVALUATE_BATCH_HARD_CAP: 30 → 90`（`discovery/engine.py`）
- `_run_batch` 的 `asyncio.gather` 调度无变化，但现在 90 条 → 3 个 batch × 30 items 并发
- `llm_evaluation_concurrency` 已有的 semaphore 兜底防止 provider rate limit

### 影响

- 单 round 评估候选从 30 → 90（3× 提速）
- 总耗时不增加（并发跑），结合 v0.3.51 的 reasoning-disabled，3 个并发 batch 总耗时 ≈ 单批 v0.3.50 一次的耗时
- LLM 月成本：单 round 提升 3×，但 v0.3.51 已经降 80%，净仍比 v0.3.50 便宜
- truncation 90% 浪费降到 ~70%（很多 round 候选不到 90 也无 truncation）

测试：830/830 通过，无新增。

---

## v0.3.51: discovery LLM 关 reasoning + style cap（2026-05-05 spec wave 1）

### 背景

跑日志诊断暴露两个问题（详见 `docs/plans/2026-05-05-discovery-runtime-fix-spec.md`）：

**U1 — discovery `evaluate_batch` 每批 8-16 分钟**：
日志数据 27 次 `discovery.evaluate_batch` 累计 ~3 小时 LLM 思考时间，最长单批 991s（16.5 min）。output tokens 8000-18000 / 30 items 主要被 reasoning chain 占用。但 evaluate_batch 任务是结构化打分（score/topic_group/style_key/franchise_key），**根本不需要思维链**。

**U5 — style 集中度无 cap**：
日志统计 13 次单 batch single style ≥ 7 条（≥23%），最高 fun_variety×10/30=33%、story_doc×11/30=37%。eval_batch 已经有 franchise cap（v0.3.50），**没有 style cap**。

### 改动

**U1 — 关闭 reasoning for 结构化任务**：

新增 per-call `reasoning_effort` 透传通道：
- `LLMProvider.complete()` ABC 加 `reasoning_effort: str | None = None` 参数
- `OpenAIProvider` / `ClaudeProvider` / `GeminiProvider`：accept + ignore（DeepSeek-only feature）
- `DeepSeekProvider.complete()`：`None` 用配置默认，非 `None` 临时覆盖 `self._reasoning_effort`，保留原 `try/finally` 语义
- `LLMRegistry.complete()` / `LLMService.complete_with_core_memory()` / `LLMService.complete_structured_task()`：threading parameter through

调用点显式 `reasoning_effort=""` 关掉 thinking：
- `discovery.engine._evaluate_batch`
- `recommendation.engine._classify_batch`（XHS classify_pool_backlog）
- `recommendation.engine._precompute_batch`（write_expression）

**保留 reasoning** 给真正需要的：`soul.speculate` / `soul.awareness` / `recommendation.delight_score`。

**U5 — `_evaluate_batch` style cap**：

跟 v0.3.50 franchise cap 同形：
- 新常量 `_BATCH_STYLE_CAP = 8`（8/30 = 27%）
- LLM 评分完成后按 `style_key` 分桶，超额按 score drop
- INFO 日志：`eval_batch style cap: dropped N (cap=8/style; offenders=fun_variety×10)`
- 跟 franchise cap 一样，empty style 被忽略（ingestion-time heuristic 默认值不会统统死锁）

### 影响

预期效果（按本次基线日志数据）：

- discovery `evaluate_batch` elapsed 从 8-16 min 降到 30s 以下（30× 提速）
- LLM 月成本下降 ~80%（reasoning tokens 是大头）
- 单 batch single-style 从 30-37% 降到 ≤27%
- 结构化输出 quality 不退化（任务不需要思考链）
- 真需要 reasoning 的 caller（speculate / awareness / delight_score）不受影响

测试：
- 修了 12 个测试 stub（accept `reasoning_effort` kwarg）+ 1 个测试用例（`test_trending_strategy_interleaves_rids_for_eval_fairness` 加 style 多样化的 LLM responses 避免新 cap 误伤）
- 830/830 通过

不动 LLM prompt builder，prompt cache 命中率不受影响。

---

## v0.3.50: discovery 三层 franchise/UP 配额（2026-05-05）

### 背景

线上日志暴露 B 站候选池被几个 hot franchise 主导：

```
01:12:46  eval_batch  top_franchise=张雪机车×13 (45%)        ← 30 条里 13 条同 UP
01:13:27  eval_batch  top_franchise=咲间妮娜×6
01:14:58  eval_batch  top_franchise=咲间妮娜×6              ← 同 UP 第三波
01:17:15  eval_batch  top_franchise=风犬少年的天空×7
```

`咲间妮娜 7+6+6 = 19 条` 横跨三个 batch，全进了池子。LLM **正确填了 franchise_key**（按 prompt 规则 7 的批内一致性约束），但下游 `_evaluate_batch` 收到 30 条里 13 条同 IP 时仍 `kept=30`——franchise 信息有，没人用。

去重只在 serve 时（`_select_diversified_batch.per_franchise_cap`），但 pool 已经被某个 franchise 占了 30+ 条时，serve 端兜底救不了池子的整体倾斜。

### 改动（三层防御）

**A. eval_batch 单批 franchise cap（`discovery/engine.py:_evaluate_batch`）**
- 新常量 `_BATCH_FRANCHISE_CAP = 4`
- LLM 评分完成后，按 `franchise_key`（lowercase）分桶，每桶超过 4 条的按 score 排序保留 top 4，其余 `score=0`（被下游 `score > 0` 过滤掉）
- INFO 日志：`eval_batch franchise cap: dropped N item(s) (cap=4/franchise; offenders=张雪机车×13)`

**B. related_chain 单 round 同 UP cap（`discovery/strategies/related_chain.py`）**
- 新常量 `_RELATED_CHAIN_PER_UP_CAP = 3`
- 一个 depth round 内沿所有 seed 收集 `batch_candidates` 时按 `up_name`（lowercase）计数，超过 3 的同 UP 不再加入
- INFO 日志：`related_chain per-UP cap: skipped N item(s) (cap=3/UP per round; 张雪机车×10)`
- **治根**：从源头不让 13 条同 UP 一起涌进 batch

**C. 入池 franchise 全局配额（`discovery/engine.py:_cache_results` + `storage/database.py`）**
- 新常量 `_POOL_FRANCHISE_QUOTA = 10`（约 pool target 600 的 1.5%）
- 新 `Database.count_pool_by_franchise()` 返回 `{franchise_key_lower: count}`
- `_cache_results` 入池前查现有 franchise 数量 + 本轮已加数量，超额拒收
- INFO 日志：`pool franchise quota: skipped N item(s) (cap=10/franchise; 咲间妮娜×7)`
- **防累积**：即便 A/B 都漏过去，pool 整体也不会被某个 franchise 占据

### 影响

- B 站 batch 内 franchise 集中度从最高 45%（13/30）降到 ≤13%（4/30）
- related_chain 沿热门 UP 链一次最多吸收 3 条，避免一个 seed 爆雷
- 单 franchise 在 pool 总量被硬上限到 10 条
- 日志可见性：所有三层 cap 命中时都有 INFO 日志，可以观察实际剧烈程度
- 改动不动 LLM prompt builder，不影响 prompt cache 命中率

测试：169/169 通过（含 2 个新回归测试）：
- `test_evaluate_batch_intra_batch_franchise_cap` — 6 条同 franchise 入 batch，验证 4 留 2 弃
- `test_count_pool_by_franchise_returns_lowercased_groups` — DB 接口返回 lowercase 分组

---

## v0.3.49: 惊喜推荐 threshold 跟 LLM rubric 对齐（2026-05-05）

### 背景

用户反馈 popup 里"惊喜推荐"数量太多。日志确认 43 分钟会话里 `Delight candidate found` 打了 35 次，单 01:05 那一波就 20+ 条。

根因：`DEFAULT_DELIGHT_THRESHOLD = 0.57` 跟 `_DELIGHT_BATCH_SCORE_SYSTEM_PROMPT` 里 LLM 自己定义的 score 标尺**对不上**：

```
prompt rubric:
  0.85+:       极少数真正「哇这个意外好对胃口」
  0.70-0.85:   跨域呼应,用户大概率会感兴趣但自己不会主动找  ← 真 delight
  0.55-0.70:   有惊喜潜力但相对常规                          ← NOT delight
  0.40-0.55:   跟用户兴趣有些关联但太普通
```

旧 threshold 0.57 落在 prompt 自己标记为「相对常规」的 0.55-0.70 区间——**LLM 都说"这不算惊喜"了，代码却推送给用户**。日志里出现的 hook 也佐证：「常规补给」「实用工具」「信息整合」「AI趣味」这种明显不是惊喜的标签都被推送。

threshold 历史轨迹：v0.3.36（0.44→0.55）→ v0.3.37（0.55→0.57）。每次加一点点，**始终没跨过 LLM rubric 的 0.70 真惊喜线**。

### 改动

`src/openbiliclaw/recommendation/delight.py`:
- `DEFAULT_DELIGHT_THRESHOLD: 0.57 → 0.70`（贴齐 LLM rubric「跨域呼应」起点）
- `CONSERVATIVE_DELIGHT_THRESHOLD: 0.67 → 0.80`（保守用户向上一档「极少数真正惊喜」靠）

新增回归测试 `tests/test_delight_scorer.py`:
- `test_default_thresholds_align_with_llm_rubric` — lock floor at 0.70 / 0.80
- `test_score_065_rejected_at_default_threshold` — 0.65 分（rubric 标的"相对常规"）必须被拒

### 影响

按本次日志数据估算（35 个 candidates 的 score 分布）：

| score 段 | 旧（≥0.57）| 新（≥0.70）|
|------|------|------|
| 0.85+ | 0 | 0 |
| 0.70-0.85 | 14 | **14**（保留）|
| 0.57-0.70 | 21 | **0**（被拒）|
| **总计** | 35 | **14** （-60%）|

- 通过的全是 LLM 自己评 0.70+ 的"用户大概率会感兴趣但自己不会主动找"
- 拒掉的 21 条全是 LLM 自己说「相对常规」的内容
- LLM 调用频率不变（仍要扫所有候选），只是 surface 变严
- 像 "常规补给" / "实用工具" / "信息整合" 这种 hook 不再触发推送

测试：26/26 通过（24 原有 + 2 新）。

---

## v0.3.48 / extension v0.3.9: 拦截"自己发的小红书笔记被推回给自己"（2026-05-05）

### 背景

用户反馈："我看到 popup 里推了好多我自己发的笔记（屎屎/三花/猫主题）"。日志确认 XHS 推荐池里大量出现用户自己发布的内容，三个来路都会污染：

- `xhs-extension-task` (XHS 关键词搜索) — xhs_producer 用用户兴趣画像生成 keyword，搜索结果**自然命中用户自己发的同主题笔记**
- `xhs-extension-explore` (XHS 推荐流) — XHS 自己的 feed 算法**会把用户自己的内容推给用户**
- `xhs-extension-profile` (bootstrap 收藏/赞过) — 偶发，自互动场景

后端 `_cache_xhs_notes` 没有任何"是否是自己"的过滤，author 字段直接落库。

### 改动

**扩展**（`extension/src/content/xhs/`，bumped 0.3.8 → 0.3.9）：
- 新 `extractSelfInfoFromState(state)` 从 XHS profile 页 state 抓 `userId` + `nickname`（已有 `extractOwnProfileUrlFromState` 提供路径模板）
- `XhsBootstrapDebugStep.self_info?: {user_id, nickname}` 字段
- `executeBootstrapTaskInPage` 在 partial / final 两个返回路径都注入 `selfInfo`，跟 task-result POST 一起回到后端。late-bound：第一阶段在 /explore 时拿不到，第二阶段进入 profile 页后立即拿到

**后端**（`api/app.py`，bumped 0.3.47 → 0.3.48）：
- `_extract_self_info_from_debug` / `_persist_xhs_self_info` / `_load_xhs_self_info` / `_is_self_authored_note` 四个 helper
- self_info 持久到 `discovery_runtime_state["xhs_self_info"]`（key-value，无 schema 变更）
- `xhs_task_result` 收到时立即 persist，并把**本次请求**的 self_info 直接传给下游过滤路径（避免 round-trip 通过 state，对 in-process test stub 友好）
- `_cache_xhs_notes` 加 `self_info: dict | None` 参数，匹配（按 nickname 或 user_id 双向匹配，case-insensitive）的 note 在入 `content_cache` 之前被丢弃，丢弃数走 INFO 日志
- bootstrap event propagation 同样 gate：自发笔记不会被当成 favorite / like 信号污染画像

### 影响

- XHS 搜索 / explore / 收藏路径回来的笔记里，author 跟登录用户匹配的**全部被拦在 content_cache 之外**——popup 不会再推用户自己的笔记
- 自发笔记也不会再以 favorite / like 的形式进入 events 表喂 soul profile（之前会让 LLM 学到"用户喜欢自己"的循环信号）
- 日志可见性：`xhs ingest filter: dropped N self-authored note(s)` / `xhs bootstrap propagate: dropped N self-authored note(s)`
- 测试：新增 `test_xhs_self_authored_notes_are_filtered`（bootstrap 带 self_info → 自发笔记不进 cache、不进 events，他人笔记照常通过）。108/108 通过

---

## v0.3.47: 推荐文案精排提前出货 — 与 discovery 各 strategy 并行（2026-05-05）

### 背景

线上日志看到一个真问题：popup 推荐卡里大量出现「《X》偏实操一点，信息是能直接拿来用的」这种 fallback 模板文案——它**就是源码里 11 套硬编码模板之一**，触发条件是候选的 `pool_expression` 字段为空。

跟踪原因：`precompute_pool_copy`（生成 expression 的那一步）排在 `_run_refresh_plan` 末尾，**所有 discovery strategy 都跑完才轮到它**。而 deepseek-v4-flash 开了 `reasoning_effort` 之后单批 `evaluate_batch` 要 8-16 分钟。一次 refresh 串行多个 strategy = 30+ 分钟之后 expression 才开始跑。这段时间内 popup 看到的内容全用 fallback 模板。

实测一份 43 分钟的 daemon 会话日志：`recommendation.write_expression` LLM 调用**只发了 2 次** → 整个会话只有 ~14 条候选拿到了真 LLM 文案，其余 95% 都是模板。

### 改动

- **`RecommendationEngine._precompute_lock`** (`recommendation/engine.py`): 新增 `asyncio.Lock` 串行化并发的 `precompute_pool_copy` 调用——多个 per-strategy fire-and-forget task 不会同时 load 相同的 un-precomputed 候选，避免对同一批 item 双开 LLM 调用浪费 token。
- **`precompute_pool_copy` 内部并行化** + **batch_size 8 → 30**: 之前 `for batch in batches: await _precompute_batch(...)` 串行，现在 `asyncio.gather` 并发。一次精排 60 条候选只要 1 个 batch latency（~30s）而不是 8 个 × 30s。
- **`_run_refresh_plan` 每个 strategy 完成后立刻 fire 一个 expression task**（`runtime/refresh.py`）: 不再等所有 strategy 跑完才统一精排。每个 strategy 完成一调 `asyncio.create_task(self._safe_precompute_pool_copy(...))`，让 expression 跟下一个 strategy 的 LLM 调用**并行**。Lock 在 engine 内串行排队，安全。最后 `await asyncio.gather` 这些 task 才进 cleanup（trim / prewarm）。
- **`_safe_precompute_pool_copy` helper**: 包装 `precompute_pool_copy` 吞掉异常 + log，给 fire-and-forget task 提供干净的失败兜底。
- **回退分支**: 整个 refresh round 没产生任何 strategy（plan 为空 / 全部 short-circuit）时仍然 sync 跑一次 `_safe_precompute_pool_copy`，保证早期 cycle backlog 还能被精排清完。

### 影响

- **expression 出货时机从「全部 strategy 跑完」提前到「第一个 strategy 跑完」**——按日志数据估算 popup 看到真 LLM 文案的延迟从 ~22 min 降到 ~5-10 min。
- **single precompute_pool_copy 内部 N 个 batch 并行**: 60 条候选从 N × 30s 降到 ~30s 全部完成。
- **Lock 防 LLM token 浪费**: 多个 fire-and-forget task 排队，不重复对同一批 item 跑精排。
- 不动 prompt builder（`build_batch_expression_prompt` 已经支持任意 batch 大小，只是默认 batch_size=8 没充分用上），LLM cache 命中率不受影响。
- 测试：`tests/test_refresh_runtime.py` 75/75 通过，更新一处 assertion（precompute_pool_copy 现在按 strategy 数被调用 N 次而不是 1 次）+ 在 `_FakeRecommendationEngine` 补 `prewarm_pool_mmr_embeddings`。

---

## v0.3.46: init 期 profile-not-ready 假错误轰炸治理（2026-05-05）

### 背景

跨日志（agent-bootstrap.log + openbiliclaw.log）联合诊断发现：daemon 启动到 soul profile 建好之间约 7 分钟里，所有依赖 profile 的后台任务都在硬调 `get_profile()`，撞上 `SoulProfileNotInitializedError`，被 `except Exception` 接住后按 ERROR / WARNING 级别打日志。**单次 init 累计 4 次 ERROR + 9 次 WARNING + 6 分钟字面截断 topic 名**——功能其实都没坏，但用户体感像装炸了。

同时 profile 建好之后，第一次 `classify_pool_backlog` 要等下一个自然 refresh tick（最多 60s），**期间 popup 看到 `topic_group` 字段空，被 fallback 退化成"屎屎/165/三花"这种从标题里抠的字面 token**。

### 改动

- **`SoulEngine.is_profile_ready()`** (`soul/engine.py`): 新增廉价、不抛异常的 profile-存在检查。后台 consumer 不再用 `try get_profile() except SoulProfileNotInitializedError` 当流控。
- **`_classify_new_pool_items` profile 未就绪时静默跳过**（`api/app.py`）: 改用 `is_profile_ready()` 前置 gate，未就绪就 DEBUG 一行返回，不再 ERROR-level 打 stack trace。
- **`CognitionCycle.run_if_due` 等 preference 层就绪**（`soul/cognition_cycle.py`）: 早期 awareness/insight 分析器在 preference 层为空时硬跑 LLM 必崩。改成在 `_run_awareness` 之前看 preference layer 是否非空，否则 `throttled=True` 静默返回。
- **`xhs_producer` 用 `is_profile_ready()` 替代 try/except**（`runtime/xhs_producer.py`）: 之前每分钟一次 `WARNING xhs producer: soul profile unavailable`，现在 DEBUG 级别静默直到 profile 落地。
- **profile-ready 转换钩子**（`runtime/refresh.py`）: `_loop_refresh` 每 tick 检测 `_is_initialized()` false→true 转换。一旦观测到，立刻调 `classify_pool_backlog(limit=100)` 把 init 窗口里堆的未分类候选一次性炒熟，不再等下个 cron tick。INFO 一行 `Soul profile became ready — kicking classify_pool_backlog`。
- **`_build_debug_summary` topic fallback 改成 `_unclassified_`**（`recommendation/engine.py`）: 候选缺 `topic_group` / `topic_key` / `tags` 时不再贪婪从标题里抠 `[一-鿿]{2,4}` 当 topic 名（之前用户日志里看到的"屎屎"/"三花"/"165"），改打字面占位符 `_unclassified_`。**diversifier 实际 bucketing 逻辑保留 fallback**（不能让所有未分类塌成一桶），只动 summary 这一层。

### 影响

- **init 头 7 分钟**：4 次 `Background pool classification failed (SoulProfileNotInitializedError)` ERROR、2 次 `Awareness analyzer failed during cognition cycle` ERROR、8 次 `xhs producer: soul profile unavailable` WARNING **全部消失**（降级到 DEBUG 或直接 silent skip）。
- **profile 一就绪立即 classify_pool_backlog**：原本要等下个 60s tick，现在同 tick 立即触发，候选 topic_group / style_key 提前 ~50s 就位。
- **summary 日志里再也看不到"屎屎/165/三花"**：未分类候选明确打 `_unclassified_`，看的人不会以为模型疯了。
- 不动任何 LLM prompt builder，不影响 LLM 缓存命中率。

---

## v0.3.45: 「换一批」恒定亚秒级 — MMR embedding 提前到 discovery 暖入（2026-05-04）

### 背景

v0.3.44 的 MMR 多样化把候选 embedding 拉到 serve() 热路径，靠 `_merge_topic_supergroups` 顺手暖到的 L1 缓存兜底。但 supergroup 用的文本 shape 是 `"{label} | {titles}"`，跟 MMR 用的 `"{title} {desc[:160]}"` 不是同一个 cache key——结果第一波 reshuffle 30+ 条候选全 miss，串行调 embedding API 把 P50 拖到 6-10s。

### 改动

- **`RecommendationEngine.warm_mmr_embeddings`** (`recommendation/engine.py`): 新公开方法，统一 MMR cache key 文本（`_mmr_embedding_text` 静态方法做 single source of truth），并行调 `EmbeddingService.embed`（自带 provider semaphore），结果落 SQLite L2 持久化。
- **`_classify_pool_backlog_locked` 持久化后立即 warm**: 每个分类批次落库成功的 item 都过一遍 `warm_mmr_embeddings`。
- **`ContentDiscoveryEngine._cache_results` detached task warm**: 主 discovery 路径每条新内容入池时 `loop.create_task(_warm_mmr_embeddings)`，不阻塞 discovery 收尾。
- **`EmbeddingService.lookup_cached`**: 新增 cache-only 同步查询接口（L1→L2，never API）。`SupportsEmbeddingService` 协议同步加签。
- **`_fetch_candidate_embeddings` 改 cache-only**: serve() 热路径**绝不**触发 provider API 调用——只查 L1/L2，miss 的 item 走 string-cap fallback 兜底。换来 <1s 的硬保证；warmer 后台填，下一次 reshuffle 自然命中。
- **`prewarm_pool_mmr_embeddings`**: 新公开方法，覆盖现有 200 条池内候选——专治升级窗口（已有 pool 早于 warm hook 落库，单靠 per-item hook 永远暖不到）。在 `restart_background_tasks` 启动时跑一次（detached task 不阻塞 API ready），并接入 refresh tick 跟 `prewarm_supergroup_embeddings` 同处。
- **MMR embedding fetch 埋点**: serve() 新增 `MMR embedding fetch: coverage=N/M elapsed=Xms` INFO，覆盖率/耗时回归立即可见。
- **`mark_pool_items_shown` 离开关键路径**: serve() 原本同步等 `mark_pool_items_shown` 提交才返回；refresh tick 的 `_enforce_pool_cap` 在 reactivate 300+ 行 `content_cache` 的瞬间会把这个 UPDATE 卡 0.5-1.5s（撞 SQLite write lock）。改成 `loop.create_task(self._mark_pool_shown_async(...))` fire-and-forget——within-session 双击重复由 `_last_served_bvids` in-memory 兜底，DB 落地稍后跟即可。配套保留 `batch_insert_recommendations_and_mark_shown` 作为可复用 API（caller 自行决定是否合并 / 异步）。
- **不动任何 LLM prompt builder**: 完全不引入新 LLM 调用，`build_batch_content_evaluation_prompt` 的 system_prompt 静态约定不变，DeepSeek/Claude/Gemini 前缀缓存命中率不受影响。

### 影响

- 「换一批」实测 30 轮（混合节奏：背靠背 / 2s 间隔 / 5s 间隔触发 refresh tick）全部 <1s。背靠背 P50≈0.61s P99≈0.85s；间隔模式 P50≈0.28s（最快 0.14s），完全没有 >1s 离群点。
- 首次 fresh-install 刷新：startup detached prewarm 跑后台填 L2，user 用啥时刻刷都 <1s。
- SQLite `embedding_cache` 表每 discovery cycle 增长 ~30-100 行，无 schema 变更。
- LLM 月支出无变化（prompt cache 命中率不动，无新 LLM 调用）。

---

## v0.3.37 / extension v0.3.5: popup 与后端实时同步修复（2026-05-04）

### 改动

- **`delight.refreshed` 实时事件**: refresh tick 末尾比较 precompute 前后 delight 候选数,新增 ≥1 时通过 WebSocket 发 `{type: "delight.refreshed", count, total_pending}` 事件。**不带 per-item payload、不触发 chrome 通知**——纯粹是触发 popup 重拉 `/api/delight/pending-batch`。修复用户痛点「惊喜推荐只有重新加载插件才出来」。
- **`pool_status` 实时事件**: `_enforce_pool_cap` 后(每分钟跑一次)如果 pool_count 跟上次发布的不同,推 `{type: "pool_status", pool_available_count, pool_target_count}`。popup `mergeRuntimeStatusEvent` 已经有 handler,会自动重渲染。修复用户痛点「滚动列表时候选池数量不变」。
- **proactive_push_interval_seconds 600→120**: 把后台兜底推送 cadence 从 10 分钟收紧到 2 分钟。主路径已经是即时 `delight.refreshed`,这里只是安全网,降低延迟尾巴。
- **popup `onEvent` 加 `delight.refreshed` 分支**: 收到事件后调 `fetchPendingDelightBatch(20)` 重拉队列,`clearDelightQueue` + `pushDelightCandidate(item)` 串接 + `renderDelightSlot()`。出错静默,下一轮 proactive 推送会自愈。

### 影响

- 新 delight 在 backend 跑完 `precompute_delight_scores` 几秒内就出现在已打开的 popup 里,无需手动重新加载扩展。
- 候选池数量在 trim/reactivate 过的 60s 内同步到 popup UI。
- `proactive_push_interval_seconds` 默认值改了,如果你的 config.toml 显式设过 600 仍会沿用,新装/默认值是 120。

---

## v0.3.36: Delight LLM JSON 解析容错（2026-05-04）

### 修复

- **`LLMDelightScorer` 不再因 provider 输出形态崩溃**: DeepSeek 严格按 prompt 返 `[...]`,但 mimo-v2.5-pro 等模型在 JSON 模式下倾向返 `{"results": [...]}` / `{"items": [...]}` / 或多个 root 对象 newline 分隔(触发 `JSONDecodeError: Extra data`)。新增 `_extract_delight_entries` 兜底:tolerant parse → 已知 wrapper 键解包(results/items/delights/data/scores/candidates/output/list/array)→ JSONL 行级回退 → single-dict-with-bvid 包装。用户切到 mimo 后 12/12 失败 → 现在全 shape 都能吞下。

---

## v0.3.35: 惊喜推荐改两段式检索（粗召 + 精排）（2026-05-04）

### 改动

- **粗召回**: `get_pool_candidates_needing_delight_score` 加 `min_relevance_score=0.55` 参数,SQL `WHERE` 加上 `relevance_score >= 0.55` 过滤。原来 SQL 只 `ORDER BY relevance_score DESC LIMIT N`,池稀疏时会喂给 LLM 一堆 weak-fit 垃圾。0.55 对齐 discovery rubric「moderate fit」基准——再惊喜也得至少半 fit。
- **精排扩容**: `precompute_delight_scores` 的 `limit` 默认 30 → 50,每 cycle 让 LLM 多看 20 条候选,提高真惊喜被命中的概率。成本从 ¥0.06/cycle 升到 ¥0.10/cycle (¥0.80/天 vs ¥0.48/天),换约 67% 更宽的搜索面。

### 思路

`relevance_score` 是 discovery 阶段 LLM 已经判过的「用户-内容匹配度」,免费可用。当作粗召回信号 + LLM-judge 做精排,经典两段式: 砍掉 95% 没望命中的低质 item,把 LLM 调用集中在最值得评判的 candidate 上。

---

## v0.3.34: 惊喜推荐改用 LLM 评分（2026-05-04）

### 改动

- **`DelightScorer` 从 embedding-cosine 升级为 LLM batch 评分**:之前的实现用 `likes_alignment` / `deep_need_alignment` / `dislike_penalty` 等 embedding 余弦相似度——但「惊喜」语义上跟「相似度高」对立(用户不喜欢「又一条 DeepSeek 测评」),embedding 越高越像反而越不惊喜。新增 `LLMDelightScorer` 类:每个 batch (默认 5 条) 一次 LLM 调用,LLM 直接按预设 rubric 判分(0-1)+ 给出 rationale + hook,**惊喜的核心判据从「相似」变成「跨域呼应 / 隐藏需求 / 概念桥接」**。
- **省掉二次 reason generation 调用**:LLM 评分时已经返回 80-180 字的 rationale 和 2-4 字 hook,直接当 `delight_reason` / `delight_hook` 写入数据库,不再单独调 `_generate_delight_reason`。
- **成本**:稳态每 cycle ~6 batch call × ¥0.01 = ¥0.06/cycle,8 cycle/day = **~¥0.48/天**;省下来的 reason generation 是 ¥0.6/天,**净改善 -¥0.12/天**。首次池子完整重打分一次性 ¥1-2。
- **`build_delight_score_batch_prompt` 在 `llm/prompts.py` 新增**:静态 system prompt(cache-friendly,符合 v0.3.28+ 规约),user payload 用 sort_keys 保证 deterministic prefix。
- **数据迁移**:删掉所有 `pool_status='fresh'/'shown'` 的老 delight_score(都是 embedding-era 标定的不可信值),让 LLM scorer 全量重判。

### 测试

- 重写 `test_precompute_delight_scores_*` 用例反映新 LLM-batch 形态,LLM mock 返回 `[{bvid, score, rationale, hook}]` 数组。

---

## v0.3.33: Delight 候选过滤修复（2026-05-04）

### 修复

- **`get_delight_candidates` 不再返回 `pool_status='suppressed'` 的 item**:之前 SQL 包含 `IN ('fresh', 'shown', 'suppressed')`,但 suppressed 是被 topic-group cap / 来源配额裁出活跃池的 item,delight 评分还挂在上面。结果 popup 每次刷新调 `/api/delight/pending-batch?limit=20` 都从 562 条 suppressed 历史评分（v0.3.32 dislike/threshold 改前打的）里捞 20 条出来,**用户每次重新加载扩展都看到 20 个看似惊喜的"幽灵推荐"**。改成 `IN ('fresh', 'shown')`,只保留活跃池。
- **一次性清理 9991 条 suppressed 状态下的 delight 残留**:`UPDATE content_cache SET delight_score=0, delight_reason='', delight_hook='', delight_notified=0 WHERE pool_status='suppressed'`。修改 SQL 后这些数据本身已不会再 leak，但清掉避免 suppressed → reactivate 时再带着老 delight 漂回来。

### 测试

- 反转 `test_database_get_delight_candidate_allows_suppressed_delight_item` 的语义：原测试用注释「虽然普通池压掉了，但这条对你还是很可能是惊喜」固化了 bug 行为，现改名 `..._excludes_suppressed_pool_items` 并断言 None。

---

## v0.3.32: Embedding 与 LLM Provider 解耦 + OpenAI 协议兼容 provider（2026-05-04）

### 改动

- **`[llm.embedding]` 拥有独立的 `api_key` / `base_url`**：embedding 不再借用 `[llm.<provider>]` 的连接，避免「想用 OpenAI 跑 embedding 但 chat 走 DeepSeek」时被迫在两处填同一个块。`build_embedding_service` 直接根据 `[llm.embedding]` 构造一个独立 provider 实例，与 chat 端 `LLMRegistry` 完全解耦。
- **新增 `openai_compatible` 一级 provider**：用于接入 Groq / Together / Azure OpenAI / vLLM / 自建等任何走 OpenAI 协议的服务。和 `[llm.openai]` 完全独立（不再用 base_url override 复用 openai block），可以同时在一个项目里跑两套（chat 用真 OpenAI、辅助任务挂 Groq 加速）。`base_url` 必填，缺失会被 `_collect_config_issues` 拦下，避免 401 hit `api.openai.com`。Embedding 段也支持选 `openai_compatible`（多数 OpenAI-compat 后端都暴露 `/v1/embeddings`，比如 Together、vLLM、Azure）。
- **向后兼容回落**：老 config（仅设了 `[llm.embedding] provider` 没填 api_key）仍可工作 —— 透明回落到 `[llm.<provider>].api_key`，并打一条一次性 WARNING 提示迁移；下个大版本会移除该回落。
- **删掉 `embedding_wants_ollama` 自动注册 hack**：embedding 现在自己构造 Ollama，chat registry 不再因为 `[llm.embedding] provider="ollama"` 而被强插一条 embedding-only 条目。
- **API 层 `EmbeddingConfigOut` 暴露 `api_key`（已脱敏）+ `base_url`**：`PUT /api/config` 接受新字段；`api_key` 字段若收到含 `*` 的回显（脱敏值原样回写），保留原值不覆盖。
- **扩展 popup Embedding 段**：新增 `EMBEDDING API KEY` / `BASE URL` 字段；provider 切换时联动模型 placeholder（`bge-m3` / `text-embedding-3-small` / `gemini-embedding-001`）和字段可见性（Ollama 隐藏 api_key、Gemini 隐藏 base_url）。删除 OpenRouter 选项（无 embedding 接口）。
- **配置渲染 / 加载同步更新**：`save_config` 写出新字段，`_build_config` 接受新字段；老 TOML（无新字段）正常加载，新字段默认 `""`。

### 影响

- 跑老 config 的用户首次启动会看到一条 `[llm.embedding] api_key/base_url is empty — falling back to [llm.<x>] credentials. ...` 的 WARNING；行为不变，按提示把凭据搬到 `[llm.embedding]` 即可消失。
- `setup-embedding` 向导和扩展的 GET/PUT `/api/config` 调用方式均无破坏性改动。

---

## v0.3.31: Discovery 来源均衡兼容小红书（2026-05-03）

### 修复

- **小红书作为一等来源族参与候选池配额**:`_SOURCE_TARGET_SHARES` 增加 `xiaohongshu`，600 池目标约分配为 `search=141 / related_chain=141 / trending=35 / explore=141 / xiaohongshu=142`。`xhs-extension-task/search/profile` 等 raw source 会归并到同一个 `xiaohongshu` 来源族，避免小红书库存在 share-aware trim 中被当作未知来源或被拆成多个来源。
- **满池时也能恢复已 suppressed 的小红书高分候选**:`reactivate_under_quota_pool_sources()` 会在来源族低于配额时，从 `pool_status='suppressed'` 且带 `xsec_token` 的可打开候选中复活一批，再由 `trim_pool_to_target_count(source_share_quotas=...)` 按统一配额裁掉过量来源。现有被压住的小红书内容不必等重新浏览同一页面才有机会回到 fresh pool。
- **池子计数排除不可打开的小红书裸 URL**:`count_pool_candidates()` 和 `count_pool_candidates_by_source()` 现在只把带 `xsec_token` 的小红书行算作可用候选，避免 runtime 状态显示“池子满了”但 UI 实际不能推荐。
- **explore 域生成遇到 DeepSeek 空内容会自愈一次**:线上日志里的 `deepseek returned empty content` 来自 DeepSeek HTTP 200 但 `content=""`，之前普通模式没有 provider 层重试，导致 `discovery.explore.queries` 直接返回 0 个探索域。`DeepSeekProvider` 现在对空内容统一重试一次；`reasoning_effort` 开启时仍关闭 thinking 重试，普通模式按原参数重试。
- **小红书 bootstrap 任务无条件前台、discovery 始终后台**:之前 `xhs-task-dispatcher` 用 `isScrollableBootstrapTask`（即 `max_scroll_rounds > 0`）来决定 bootstrap 是否前台,所以若有用户用 `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS=0` 跳过滚动会落到后台拉数据。语义改成「init-time bootstrap 始终前台 + discovery (search/creator) 始终后台」: bootstrap 是用户跑 `openbiliclaw init` 时主动期望看到的过程(透明性),且 XHS 虚拟列表只在 active tab 才正确分页;discovery 是后台连续扫描,不该打扰用户活跃浏览。
- **Ollama embedding 在系统代理环境下全失败**:用户开了本地 HTTP 代理（如 7897 端口的 VPN 客户端）时，`httpx.AsyncClient` 默认 `trust_env=True` 会把 localhost embedding 请求也走代理 → 全部 `httpx.ReadTimeout`。日志统计显示一天 140+ 次失败，**直接拖垮惊喜推荐**：`DelightScorer` 的 `likes_alignment` / `deep_need_alignment` / `dislike_penalty` 全返 0，99.5% 池内 item（604/607）落到 0.01-0.50 区间永远过不了 0.65 阈值。`OllamaProvider.embed()` 现强制 `trust_env=False`，绕开代理直连本地 Ollama。
- **EmbeddingService 缓存被空向量永久污染**:embedding 是用户配置 `provider="ollama"` 时的**主路径**（不是降级），但 `EmbeddingService.embed()` 之前会无条件把 provider 返回的 `[]` 也写进 L1 + L2 缓存。代理 bug 那段时间 ~140 次失败把 170 条核心 likes 文本（`游戏攻略` / `动漫杂谈` / `洛克王国` / `金铲铲之战` 等）全部毒化为空向量 → 即使修了代理，DelightScorer 永远从缓存拿到空列表 → likes_alignment 永远返 0。新增空向量守卫：provider 返 `[]` 时跳过缓存写入、打 WARNING 让失败模式在服务层可见而不是埋在 provider 日志里；同时清理了 `data/embedding_cache.db` 里已经被毒化的 170 条历史数据。
- **EmbeddingService 并发把本地 Ollama 打爆**:proxy fix 之后 daemon 立刻用并发 embed 补齐积压（delight scoring + 主题去重 + speculator + 池内 candidate batch 同时发起），实测一秒内 14+ 个并发请求灌进 bge-m3 单进程 GGUF runner，CPU 4 核 100%、`ollama runner` 占用 406%、curl 直连 30s 都收不到响应、所有 in-flight 请求 60s timeout 失败。新增 `EmbeddingService` 内部 `Semaphore(2)` 限流（默认 2，可通过 `max_concurrent_provider_calls` 改），同时把 `OllamaProvider.embed` 的 httpx timeout 从 60s 提到 120s 吸收冷启动 + 队列等待。
- **Speculator 探针长复合中文短语永远匹配不上事件**:LLM 生成的 probe 域名常是 `'AI图像生成工作流深度拆解'` 这种 13 字连续中文，原匹配器三条路径全失效（整串 substring 不命中、`[与和·、/\s及]+` 切不动、whitespace-tokenize 只产 1 个 token）→ 一天观察 0 次匹配，所有探针挂在 active 槽 3 天后 TTL 过期被拒。新增 Chinese-bigram 兜底：name 端要求 ≥4 个 distinct bigram、event 端要求 ≥2 个 bigram 重叠才算命中，配合上游 `confirmation_threshold=3` 防误升。
- **Speculator "generated N new" 日志骗人**:`result.generated` 之前取 `state.active` 全集，导致每轮 tick 都把携带过来的老探针重复打成 "generated 2 new"，制造在工作的假象。改成取 `_generate` 调用前后的 domain 集合差，只展示真正新增的；空集时落到 `force_tick: no-op (active full)` DEBUG 行。`Speculator observed` 日志同步从 DEBUG 升到 INFO，让事件→探针确认信号在生产日志里可见。
- **Speculator slot-aware 提早 skip LLM 调用**:`_should_generate` 之前只检查 `active_count < max_active`,但 LLM 几乎肯定会重复提案已存在 active 集合中的 domain → dedup 之后净新增 0。要求至少 2 个空闲 slot 才发起 LLM 调用,否则跳过。粗略估算每天省 ~¥0.04 的 speculator 浪费调用。
- **CLI 三个 Ollama 探测**(`_ollama_is_running` / `_ollama_has_model` / `_ollama_pull_model`)同样存在代理劫持问题,补 `trust_env=False`,避免 `setup-embedding` 在代理环境下误判 "Ollama 没启动"。
- **DelightScorer 增加 embedding 子系统死亡告警**:四个 embedding-driven 信号(likes / deep_need / insight / dislike)同时为 0.0 时,几乎只可能是 embedding 子系统挂了(用户的 likes/deep_needs/insights/disliked_topics 同时为空在稳态下不可能)。新增 per-candidate WARN 让失败信号在 recommendation 层可见,不再被埋在 1GB 的 provider HTTP DEBUG 里。
- **`trim_topic_group_overflow` 每分钟一行 INFO 噪音降级**:稳态下池子里 `人工智能:8 over cap` 这种数据每 60s 重复打一遍,一天 1440 条。Database 里的 emit 改成 DEBUG;Refresh 层的 `enforce_pool_cap: reactivated=N` 加 fingerprint 缓存,reactivated 数与上一 tick 相同则降到 DEBUG,变化时才 INFO。
- **EmbeddingService L1 cache 改 LRU**:之前用普通 dict + `next(iter)` 驱逐最老,实质是 FIFO,500 条容量 + bursty 访问下会驱逐刚刚命中过的热 key。改用 `OrderedDict` + `move_to_end(key)` on hit + `popitem(last=False)` on evict,正确 LRU。
- **OllamaProvider 加 1 次重试**:bge-m3 短暂 OOM / Ollama runner 重启 / 模型 hot-swap 这些瞬时故障之前直接返 `[]` 走静默降级。改成 `for attempt in (1, 2)` 模式,首次失败 DEBUG 一行后立刻重试,两次都失败才 WARN。同时把 `Ollama embedding failed` 日志改成 `failed after 2 attempts`。
- **`config.toml` 同步 v0.3.30 logging 默认值**:把用户旧的 `max_file_size_mb = 1024` 降到 100,补上 `aggregate_budget_mb = 500` / `unmanaged_truncate_mb = 200` / `unmanaged_max_age_days = 30`,让 v0.3.30 引入的日志兜底机制实际生效。这个改动只动 `config.toml`(gitignored),仓库 `config.example.toml` 早就是新值。
- **DelightScorer dislike_penalty 阈值/放大器按 bge-m3 重新标定**:之前 `(sim - 0.55) * 2.5` 是按 Gemini 标的,bge-m3 对低语义中文(直播片段标题、metadata)有"通用中文 cluster"现象,baseline cosine 0.78-0.85,所有候选都被 dislike 拉减 0.30 分。改成 `(sim - 0.78) * 1.5` 后:历史 3 条 ≥0.65 delight item 重打分从被 dislike 假阳性压到 0.20 → 恢复到真实 0.51-0.52,新候选最高 likes 也从被压到 0.13 → 真实 0.40-0.48。
- **DelightScorer threshold 同步按 bge-m3 实际分布下调**:0.65/0.75 默认是按 Gemini embedding 标的,在 bge-m3 上等于"永远不触发 delight"。基于实测 100 条池内 top-relevance 候选的实际分数分布(max=0.485, p95=0.440, p90=0.428),`DEFAULT_DELIGHT_THRESHOLD` 从 0.65 改成 0.45(对应 ~p95 的"特别匹配"位置),`CONSERVATIVE_DELIGHT_THRESHOLD` 从 0.75 改成 0.55。
- **DelightScorer "embedding 子系统死亡"告警改用直接探测**:之前判定条件是 4 个 embedding 信号同时为 0,但一个用户兴趣范围之外的合法内容(如 tech-only 用户看到一条历史纪录片标题)也会全 0,导致告警每条 candidate 都 false-positive。改成单次 `embed(content_text)` 探测,只有 provider 真返空向量才告警。

### 测试

- 新增 storage / refresh runtime 回归测试覆盖小红书来源族归一、under-quota suppressed 复活、满池裁剪传递小红书配额。
- 新增 LLM provider 回归测试覆盖 DeepSeek 普通模式空内容重试。
- 新增 `test_observe_matches_long_chinese_composite_phrase` 覆盖 bigram 匹配兜底（命中真实标题、不误中无关内容）。

---

## v0.3.30: 日志自动清理（按大小 / 按年龄 / 按总预算）（2026-05-02）

用户实测发现 `logs/` 目录下有几个未托管的大文件占盘:`backend-restart.log` 2.2 GB、`openbiliclaw-restart.log` 296 MB,加上原本的 `openbiliclaw.log` 1 GB 主日志,整个目录 5 GB+。原 `RotatingFileHandler` 只管 *本身配置的那个* 文件,其他 stdout-redirect 出来的脚本日志完全没人管。补一套 unmanaged 日志兜底清理。

### 新增

- **启动时自动 sweep `logs/` 目录的 unmanaged 文件**(`logging_setup._sweep_unmanaged_logs`):
  1. 单文件超过 `unmanaged_truncate_mb` MB → 直接 `truncate` 为 0(留一行 marker)。专治 `backend-restart.log` 这类被脚本无限 append 但项目代码控制不到的文件
  2. mtime 超过 `unmanaged_max_age_days` 天 → 直接删除
  3. 整个 logs/ 目录(含 managed)总大小超过 `aggregate_budget_mb` MB → 按 mtime 从最旧的 *unmanaged* 文件开始删,直到回到预算内。**Managed 文件(`<filename>` + `<filename>.N`)永远不被这个 pass 删**(rotation 自己管)

  每个 truncate / delete 都打 INFO 日志,daemon 启动时 tail 一眼能看到清了什么
- **`openbiliclaw logs-prune` CLI**(默认 dry-run)—— 手动触发兜底清理,可临时用更激进 / 更保守的阈值。`--apply` 才真改文件。Rich 表格按 traffic-light 色显示 keep / truncate / delete (age) / delete (budget) 四种 plan
- 4 个新单测覆盖 truncate / age delete / aggregate budget eviction / sweep_unmanaged=False 跳过

### 默认值变化(影响新装)

- **`max_file_size_mb` 1024 → 100**:1 GB 单文件太大,绝大多数 daemon 跑两天就把磁盘吃掉一截。100 MB × 2 backups = 200 MB 上限,够 1-2 周 INFO 级日志
- **`aggregate_budget_mb = 500`**(新):整个 `logs/` 目录总磁盘预算 500 MB,unmanaged 超出按时间评最早删
- **`unmanaged_truncate_mb = 200`**(新):单文件超过 200 MB 直接 truncate
- **`unmanaged_max_age_days = 30`**(新):30 天前的 unmanaged 文件直接删

### 修改

- `LoggingConfig` 加 3 个新字段(`aggregate_budget_mb` / `unmanaged_truncate_mb` / `unmanaged_max_age_days`),旧 config.toml 没有这些字段也兼容(用 dataclass 默认值)
- `configure_logging` 新增 `sweep_unmanaged: bool = True` kwarg。CLI `_initialize_logging` 检测 `logs-prune` 命令时传 `False`,避免 dry-run 被全局 callback 顺手清掉(否则 dry-run 等于自动 apply)
- `config.example.toml` 同步更新,加上 4 行注释说明每个阈值的意义

### 修复

- **扩展自动同步 B 站 Cookie 的首装竞态**:如果扩展已安装但本地后端还没起来,之前首次 POST 失败后要等 cookie 变化或最长 1 小时 alarm 才会重试,导致 AI agent 一句话安装后看起来"自动获取不到 Cookie"。现在 service worker 冷启动会启动 cookie sync,POST 失败时把 alarm 临时切到 1 分钟重试,成功后恢复 60 分钟刷新;`startCookieSync()` 也改成真正幂等,避免重复注册 `chrome.cookies.onChanged` 监听器。
- **后端可主动要求扩展回传 Cookie**:`/api/runtime-stream?client=background` 建连时,如果后端解析不到 B 站 Cookie,会先发 `bilibili_cookie_sync_requested`;扩展收到后立即 POST 当前浏览器 Cookie 到 `/api/bilibili/cookie`。这让后端启动后不用等下一轮 alarm,能主动拉起一次 Cookie 同步。
- **AI agent 一句话安装不再跳过 embedding / 小红书确认**:`agent_bootstrap.py` 新增 `--yes-xhs` / `--no-xhs` 并在 auto-init 前检查两个显式决策:embedding 方案和小红书收藏 / 点赞 opt-in。凭据齐全但没问这两项时,bootstrap 返回 `status=needs_decisions` 而不是直接跑 `openbiliclaw init`;install.sh / install.ps1 的状态块会把默认 `--embedding-provider ollama --embedding-model bge-m3 --no-xhs` 示例命令打印出来,让智能体必须先问用户再继续。
- **插件推荐列表滚到底续页不再卡住**:side panel 推荐 tab 在首次渲染、切回推荐页和追加完成后都会重新检查一次底部距离,不再只依赖新的 scroll 事件触发 `/api/recommendations/append`。
- **插件初始化后不再误显示 init 提示**:popup 空推荐状态会优先识别 `manual_refresh_state=running`、pending signal 和候选池补货信号;初始化后首轮补货 / 池子已有内容但 `initialized` 标记短暂滞后时,不再继续显示“还没完成初始化”。
- **插件发布版本推进到 `extension-v0.3.3`**:本次插件 release 包含 Cookie 自动同步竞态、推荐续页和初始化状态提示修复。

### 测试

- 全套 944 通过 / 16 失败(基线) / 15 跳过 — 0 新回归

---

## v0.3.29: prompt-cache 通用化改造 + 命中率观测 + Claude 显式 marker（2026-05-02）

为 daemon 长跑成本拉低 50-80% 做架构性铺垫。挖到 v0.3.26 计费台账没有 cache 字段(provider 报但没归一化),v0.3.27 prompt builders 多个把 per-call 变量塞进 system 消息(让 provider-side 自动缓存命中率永远是 0),Claude 这种"显式 marker 才激活" 的 provider 完全没接入。三个层一起改。

### 新增 (Layer 3 — 跨 provider 的命中率观测基础)

- **每家 LLM provider 提取 cache 字段并 normalize 到 `LLMResponse.usage["cached_input_tokens"]`** —— OpenAI 系 (`prompt_tokens_details.cached_tokens`)、DeepSeek (`prompt_cache_hit_tokens`)、Claude (`cache_read_input_tokens`,另外保留 `cache_creation_input_tokens` 单独记账)、Gemini (`usage_metadata.cached_content_token_count`),OpenRouter / 中转站 / 国产官方因为继承 OpenAIProvider 自动获益
- **`pricing.CACHE_HIT_DISCOUNT`** 表 + `estimate_cost(..., cached_tokens=N)` 扩展 —— 各家 cache 折扣率列表(DeepSeek 0.10 / OpenAI 0.50 / Claude 0.10 / Gemini 0.25 / Ollama 0 / 未知 0.5),split prompt_tokens 按 cached/non-cached 分别计费
- **`Database.llm_usage` 加 `cached_input_tokens` 列 + migration `_ensure_llm_usage_cache_columns`** —— 存量 DB 自动 backfill,新调用按 cache 折扣存账。`query_llm_usage_by_caller` / `_total` / `_since_id` 全部返回 cache 字段
- **`UsageRecorder` 提取 cache 字段并写库** —— INFO 日志多了 `cache_hit=4000/8500 (47%)` 注释,直接 tail daemon 看实时命中率
- **`openbiliclaw cost --by caller` 加 cache 命中率列** —— 红 (<30%) / 黄 (30-60%) / 绿 (>60%) 三色,红色 caller = prompt 前缀有污染,直接定位到要 audit 的 builder
- **`init` 收尾的 cost summary 也展示 per-caller cache 命中率** —— 跑完一次 init 直接看命中分布

### 重构 (Layer 1 — 让 system_prompt 100% 静态以激活 provider 缓存)

之前 audit 出 `build_batch_content_evaluation_prompt` / `build_content_evaluation_prompt` / `build_recommendation_expression_prompt` / `build_batch_expression_prompt` / `build_delight_reason_prompt` 这 5 个最热点的 builder 都把 `source_hint` / `_platform_friend_label` / `_platform_content_label` / `_render_tone_profile` 拼接到 system_prompt,**每次切 strategy / platform / 用户 → 整个 ~3500 token 的 system prompt 失配,provider 自动 cache 永远命不上**。改造成"system 100% 静态 + 所有变量挪到 user_prompt 前缀":

- 5 个 builder 全部用 module-level 常量 `_<NAME>_SYSTEM_PROMPT` 表达 system,每个常量都是字符串字面量(不能 f-string,不能拼接,不能 substitute);所有原 system 里的变量(source_context / source_platform / tone_profile / friend_label / content_label)挪到 user_prompt
- user_prompt 顺序: 平台 / 上下文 / tone (semi-stable per user) → profile (slow-changing) → content_batch (every call)。这样 provider auto-cache 不仅命中 system,顺序合理时还能延伸命中 user 前缀
- JSON 序列化全部加 `sort_keys=True`,防止 dict 顺序变动让 cache miss
- system 里加一句 "下面 user 消息会给出 <X>(...)" 让 LLM 明确知道去哪里读变量(prompt engineering 上不损失)

### 例外 (Layer 1 单用户场景下保留 user-specific system)

- **`build_socratic_dialogue_prompt` 保持原样** —— 它的 system 包含 friend_label / tone / core_memory_text。在 OpenBiliClaw 这种**单用户场景**下,per-user 状态在该用户的多次调用里稳定 → cache 仍命中。多用户部署才需要重构,目前不必

### 工程纪律 (Layer 4)

- **`CLAUDE.md` 新增 "LLM Prompt-Cache Convention" 段** —— 给未来贡献者立规则:任何新 prompt builder MUST 满足 system 100% 静态,JSON 序列化必须 deterministic,所有变量入 user_prompt
- **`test_llm_prompts.py::test_prompt_builder_system_messages_are_call_invariant`** —— 自动化兜底:遍历所有 prompt builder,两组不同 input → assert system msg byte-identical,违反则报错并指明 cache-poisoning builder

### Layer 2 — Claude 显式 cache marker

- **`ClaudeProvider` 自动给 system message 打 ephemeral cache_control 标记** —— Anthropic prompt cache 是显式机制,纯字符串 `system="..."` 永远不缓存,必须用 list-of-blocks 形式 + `cache_control: {"type": "ephemeral"}` 才会激活。新增 `_render_system_param()` 把 system 文本包成单 block 列表 + cache marker,5min TTL,90% off on cache reads,首次写 +25% 加价。系统 prompt 短于 per-model 阈值时(Sonnet 1024 / Opus-Haiku 2048 token)Anthropic 静默忽略 marker,所以这个改动对短 prompt 也安全
- 2 个新单测 covering: marker 正确插入到 system list-of-blocks 形式,以及 `cache_read_input_tokens` / `cache_creation_input_tokens` 通过 `LLMResponse.usage` 正确流转

### 仍未做(deferred)

- **Gemini 显式 Context Caching API** —— Gemini 的 prompt cache 不是 in-line marker,而是另起一个 `cachedContents.create()` API 提前上传 stable 部分得到 `cache_id`,然后调 `complete()` 时引用 cache_id。需要 cache_id LRU 池 + TTL 管理,改动量比 Claude 大得多。先观察 Layer 3 数据 —— 如果用 Gemini 的人多且命中率确实低,再投资

### 测试

- 8 个新单测覆盖 cache 折扣计算 / per-caller 持久化 / 跨 provider 命中字段 round-trip / Claude cache_control marker 注入 / Claude cache_read+creation token 提取
- audit invariant 测试覆盖 6 个 cache-friendly builder
- 全套 940 通过 / 16 失败(基线) / 15 跳过 — 0 新回归

### 预期效果

- DeepSeek 默认场景:`discovery.evaluate_batch` 5 次 strategy 评估,从原本 5 次 cold(~17500 input tokens 全收钱)→ 第 1 次 cold + 后 4 次命中 ~3500 token system,**该 caller 总成本立即砍 60-70%**
- 同效果适用于 `recommendation.evaluate_batch` / `_expression` / `_delight_reason` / `_content_evaluation`
- OpenAI 50% / Claude 90% / Gemini 75% cache 折扣,自动派(DeepSeek/OpenAI/中转站)无需改 SDK 调用,显式派(Claude)由 ClaudeProvider 内部自动注入 marker
- 跑一段时间后 `openbiliclaw cost --by caller --days 7` 应该能看到顶层 caller 的命中率从 0 跳到 60-80%

### 下一步

- Gemini 显式 Context Caching 等数据驱动决策(见上 deferred 段)
- 数据驱动的优化:看 `--by caller` 命中率 < 60% 的 caller,逐个 audit 是不是新加的 builder 没遵守 cache 公约

---

## v0.3.28: LLM 费用观测全链路打通（caller 标签 + 实时日志 + per-init 总结）（2026-05-02）

之前 `UsageRecorder` 的 `caller` 字段虽然在表结构 + recorder API + DB 查询里都已就位,但**整个代码库里没有一个 LLM 调用点真的传 `caller="<module>"`** —— 所有行的 caller 都是空字符串,意味着当年设计的 per-module 费用 attribution 完全失效,`openbiliclaw cost` 能看到 by-day / by-provider/model 但看不出"钱花在哪一层",这是用户最关心的视角。补全:

### 新增

- **27 个 LLM 调用点全部 wire 上 caller 标签** —— 覆盖 `recommendation.evaluate_batch / .delight_reason / .write_expression / .expression`、`discovery.trending.rids / .search.queries / .explore.queries / .evaluate_single / .evaluate_batch`、`eval.scenario_gen / .relevance / .specificity / .query_quality`、`soul.preference / .preference.chunk / .profile_build / .insight / .awareness / .role_update / .values_update / .core_update / .speculate / .dialogue / .dialogue.tools / .dialogue.tool_followup / .dialogue_insight`、`sources.{platform}.extract / sources.xhs.keyword_gen`、`api.sentiment`。还把 `LLMService.complete_with_tools` / `complete_socratic_dialogue` 也加了 `caller` 形参并 forward 到内部 `complete_with_core_memory` —— 之前这两个方法漏接 `caller`,让 dialogue 路径的费用全归到 untagged
- **`UsageRecorder.record()` 每次 LLM 调用打 INFO 日志** —— `[llm-cost] caller=discovery.evaluate_batch model=deepseek-v4-flash tokens=850→230 ≈ ¥0.0010`。tail daemon 日志 (`journalctl -fu openbiliclaw` / `docker logs -f openbiliclaw-backend`) 就能看费用实时累积,不用等跑完才查
- **单次调用超阈值时打 WARN** —— 默认 ¥0.10 阈值(可通过 `OPENBILICLAW_LLM_EXPENSIVE_CNY` 环境变量调)。抓 runaway prompt(忘了截断历史 / 误开 reasoning_effort=max / 单 batch 太大)用,WARN 行包含 caller / model / token / 实际花费,定位很快
- **`openbiliclaw cost --by caller`** —— `cost` CLI 加了第三个表(by-caller),展示按模块的费用占比 + token 数。`--by all`(默认) / `--by day` / `--by provider` / `--by caller` 四档
- **init 结束时自动打印本次 init 的 cost summary** —— 不用再手动 `openbiliclaw cost`,init 完成后直接显示按 caller 拆分的费用占比(本次 init 总 N 次调用 ≈ ¥X,其中 discovery.evaluate_batch 占 60% / soul.profile_build 占 15% 等)。靠 `Database.max_llm_usage_id() / query_llm_usage_since_id()` 在 init 入口快照行 id,出口反查,把累积 usage 限定到本次 init 窗口
- `pricing.py` 加常量 `EXPENSIVE_CALL_CNY_THRESHOLD = 0.10`(可环境变量覆盖)

### 修改

- `Database.query_llm_usage_by_caller(days=N)` 新方法,SQL 按 caller 分组聚合,`ORDER BY cost_cny DESC` 让最贵的调用排第一
- `LLMService.complete_with_tools` / `complete_socratic_dialogue` 签名加 `caller: str = ""`,forward 到 inner `complete_with_core_memory(caller=caller)`

### 测试

- 修了 ~30 个测试 fake 让它们的 `complete_*` 签名也接 `caller` 形参(否则生产调用点传 `caller=...` 会让 fake 报 TypeError)。批量改了 17 个测试文件
- 全套测试 16 失败 / 931 通过,跟 baseline 完全一致 —— 0 新回归

---

## v0.3.27: 安装文档全面同步至 init wizard 当前形态 + DeepSeek V4 默认模型（2026-05-02）

### 修改

- `docs/openclaw-quickstart.md` —— 把 `init` 4 阶段向导描述同步到 v0.3.27+ 当前形态:Phase 1 LLM(DeepSeek 默认 / Ollama+网关收进高级)、Phase 2 配置、Phase 3 Embedding(Ollama bge-m3 默认)、Phase 4 Per-module 覆盖。新增独立的 🌸 小红书数据可选问题(在 wizard 之后、数据拉取之前),并明确"扩展会在浏览器开前台 tab 抢一次焦点"的真实行为。`init` 阶段列表新增可选小红书拉取步,并提示用 `openbiliclaw cost` 查看花费
- **DeepSeek 默认模型 `deepseek-chat` → `deepseek-v4-flash`** —— 旧 `deepseek-chat` / `deepseek-reasoner` DeepSeek 官方将于 2026/07/24 弃用。`config.example.toml` 早就指向 v4-flash,但 `cli.py` `_PROVIDER_DEFAULTS` 还在写 `deepseek-chat`,导致 init 向导给出过期的默认值。修复点:`_PROVIDER_DEFAULTS["deepseek"].model`、`_LLM_MENU` hint、Phase 2 配置阶段新增 `_PROVIDER_MODEL_HINT` 表(每个 provider 在 prompt 模型名前显示一行可选清单,DeepSeek 那行明确列 v4-flash / v4-pro 两档 + 旧名弃用日期),让用户明确确认而不是回车跳过一个看不懂的字符串。同步更新 `docs/{openclaw-quickstart,docker-deployment,agent-install,agent-deployment,modules/config,modules/llm}.md`、`scripts/agent_bootstrap.py` 示例、`extension/popup/popup.html` placeholder、`pricing.py` 加 `deepseek-v4-pro` 行
- **OpenAI 协议兼容: 9-preset 子菜单 (Kimi / MiniMax / 通义 / 智谱 / Yi / 中转站 / 自建 / Azure / 其它)** —— 之前选第 7 项 "OpenAI 协议兼容" 就掉到一个让用户手填 Base URL + 模型名的裸 prompt,普通用户不知道每家的 endpoint 长什么样,中转站 / Azure / vLLM 三种用法的差异也没说清。新增 `_OPENAI_COMPAT_PRESETS` 表 + `_prompt_openai_compat()` helper:选第 7 项后弹出 9 行子菜单,**Base URL + 默认模型按 preset 自动填好**(Kimi `api.moonshot.cn/v1` + `moonshot-v1-8k`;MiniMax `api.minimaxi.chat/v1` + `abab6.5s-chat`;通义 `dashscope.aliyuncs.com/compatible-mode/v1` + `qwen-plus`;智谱 `open.bigmodel.cn/api/paas/v4` + `glm-4-flash`;Yi `api.lingyiwanwu.com/v1` + `yi-medium`;中转站 / Azure / vLLM-LMStudio 也都各自有合理的 prompt 引导)。每个 preset 在 prompt 模型名前显示该家的"可选模型"清单。同步 `docs/{openclaw-quickstart,docker-deployment,agent-install}.md` 全部展开 9 个 preset 的清单,AI agent 注释里加"看到 Kimi / 通义 / 智谱 / Yi / Moonshot / MiniMax / Qwen / GLM / 中转站 / OneAPI / Azure / vLLM / LMStudio 等关键词时,优先引导走第 7 项子菜单"
- **默认模型全面刷新到 2026-05 当前线上(之前几乎全部过期)** —— 用户实测发现 init 向导推的默认模型几乎都已停服或被替代。Web 搜索确认每家当前线上情况后,逐项更新 `_PROVIDER_DEFAULTS`、`_LLM_MENU` hint、`_PROVIDER_MODEL_HINT`、`_OPENAI_COMPAT_PRESETS`、`config.example.toml`、`pricing.py`:
  - **OpenAI**: `gpt-4o-mini` → `gpt-5-nano`(GPT-5 nano 是当前最便宜款 $0.05/$0.4 per M;gpt-4o 系列 2026-02 已从 ChatGPT 退役)。完整可选: gpt-5-nano / gpt-5.4-nano / gpt-5.4-mini / gpt-5.5(4/2026 旗舰)/ gpt-5.5-pro
  - **Claude**: `claude-sonnet-4-5-20250929` → `claude-sonnet-4-6`(Sonnet 4.6 1M ctx)。完整: claude-haiku-4-5(便宜)/ sonnet-4-6(默认)/ opus-4-7(旗舰 / agentic 最强)
  - **Gemini**: `gemini-2.0-flash-exp` → `gemini-2.5-flash`(2.0-flash-exp 已淘汰)。完整: 2.5-flash(默认)/ 3-flash-preview(新)/ 3.1-pro(旗舰)/ 3.1-flash-lite-preview(最便宜)
  - **OpenRouter**: `openai/gpt-4o-mini` → `openai/gpt-5-nano`(对齐 OpenAI 默认)
  - **Ollama**: `llama3` → `qwen2.5:7b`(项目中文优先,qwen2.5 比同尺寸 llama3 中文好得多)
  - **Kimi**: `moonshot-v1-8k`(2026-05-25 停服)→ `kimi-k2.6`(最新 / 256K ctx / 多模态)。Base URL `api.moonshot.cn/v1` → `api.moonshot.ai/v1`(国际站为主)
  - **MiniMax**: `abab6.5s-chat`(已被 M 系列替代)→ `MiniMax-M2.7`(4/2026 / 228K ctx / $0.30 ~ $1.20 per M)。Base URL `api.minimaxi.chat/v1` → `api.minimax.io/v1`
  - **通义**: 仍用 `qwen-plus` 别名(自动跟最新快照,当前 → qwen3.6-plus)。endpoint 不变
  - **智谱 ChatGLM**: `glm-4-flash` → `glm-4.7-flash`(1/2026 发布的免费旗舰 / 200K ctx);可选 `glm-5`(2/2026 付费旗舰 / 745B MoE)
  - **Yi**: 仍用 `yi-medium`,在 hint 里加上 `yi-lightning`(新 / 快)
  - **DeepSeek**: ✅ 之前修对了,仍是 `deepseek-v4-flash`/`deepseek-v4-pro`
  - **pricing.py**: 加 GPT-5 / Claude 4.6+ / Gemini 3.x / Kimi K2.6 / MiniMax M2.7 / Qwen flash-plus-max / GLM 4.7-flash + 5 / Yi spark-medium-large 的单价行,旧 V3/V4o/Sonnet 4.5 等保留兼容
- **OpenAI 协议兼容引导深度补强** —— 之前 9-preset 子菜单只解决了 "Base URL + 模型自动填" 一层,用户实际还会卡在"在哪里申请 Key / 这家服务到底是干嘛的 / 选完之后 embedding 怎么办"这三个问题。每个 preset metadata 扩展为 `description` / `signup_url` / `domain_alt` / `supports_embedding` / `embedding_alt`,`_prompt_openai_compat()` 重写为四段式引导:
  - **选完后展示一段服务介绍**(Kimi → "国产长上下文老牌 256K ctx,长文档理解强";MiniMax → "代码 / agent 场景 SOTA,$0.30/$1.20 per M";智谱 → "GLM-4.7-Flash 完全免费,GLM-5 是 Claude Opus 级")
  - **直接打印 Key 申请链接**(国内/国际两个地址都列),用户 cmd-click 就能去注册
  - **国内域名替代提示**(Kimi `api.moonshot.cn/v1`;MiniMax `api.minimaxi.com/v1`)
  - **预提醒 embedding 怎么办**: Kimi / MiniMax / Yi / 自建 没 embedding endpoint(打印黄色 ⓘ 提醒 Phase 3 自动 fallback Ollama bge-m3,免费 / 离线);Qwen / GLM / Azure / 中转站 有 embedding(打印 💡 提示 Phase 3 高级选项可指向同一 base_url)
  - **结尾打印将写入的 (base_url, model) 二元组**,catch typo
- **`scripts/agent_bootstrap.py --llm-preset {kimi,minimax,qwen,zhipu,yi,self-hosted,relay,azure,custom}`** —— AI agent 驱动的非交互式安装路径补一刀。之前 AI agent 用 `--llm-base-url` + `--llm-model` 配 OpenAI 兼容服务时,得自己记住每家的 endpoint(经常写错);现在 `--llm-preset kimi` 一句话搞定,base_url 和默认模型从 `LLM_PRESETS` 表里取(和 cli.py 的 `_OPENAI_COMPAT_PRESETS` 同步)。隐式锁 `--provider=openai`,显式传不同 provider 会冲突报错。`--llm-base-url` / `--llm-model` 可以 per-field 覆盖 preset 默认。`docs/agent-install.md` 加 8 行示例(每家服务一行)
- **OpenAI 协议兼容子菜单 — 中转站(relay) 提到第 1 位 + 主菜单第 7 项 label 突出"中转站"** —— 复盘发现协议兼容选项的真正主流场景是"我买了中转站 / OneAPI Key,想用人民币付钱跑 OpenAI/Claude/国产模型"。之前菜单按"国产官方 → 自建 → 中转站 → Azure → 其它"排序,把最常见的中转站埋在第 7 个,普通用户得先翻过 5 个国产官方项才看到自己的选项。重排为:relay 第 1 位(default,带 ★ 标记 + "大多数人选这个"标注) → Kimi/MiniMax/Qwen/Zhipu/Yi 国产官方 → Azure → 自建 → custom 兜底。同步:主菜单第 7 项 label 改为"中转站 / OpenAI 协议兼容服务(OneAPI / 团队网关 / 国产官方 / Azure / 自建)";子菜单 intro 显式区分三类用户(中转站 / 国产官方 / 企业 Azure-自建);`docs/{openclaw-quickstart,docker-deployment,agent-install}.md` 同步重排表格 + 补"国内绝大多数中国用户选这个就对了"框架

---

## v0.3.26: LLM 计费模块 + 默认配置成本调优（2026-05-02）

新增本地 LLM 用量与花费追踪,顺手把 `config.example.toml` 里几个会让新装用户立刻烧钱的默认值改了。重启 daemon 后,跑 `openbiliclaw cost` 就能看每天实际花了多少。

### 新增

- **`openbiliclaw cost` CLI 命令** —— 显示最近 N 天 LLM 调用的按天 / 按 provider/model 分布,以及估算花费。每次成功 LLM 调用都会写一条到 `llm_usage` 表(timestamp / provider / model / caller / tokens / 估算单价)。`UsageRecorder` 是单点 hook,挂在 `LLMService.complete_with_core_memory` 之后,失败被吞,不影响业务热路径
- `src/openbiliclaw/llm/pricing.py` —— DeepSeek / OpenAI / Claude / Gemini / OpenRouter / Ollama 的 CNY 单价表,USD 系预乘 7.2 让账面统一。未知 provider 走通用 fallback 而不是静默 0
- `Database.insert_llm_usage` / `query_llm_usage_by_day` / `query_llm_usage_by_provider` / `query_llm_usage_total` —— 新表 `llm_usage` + 4 个查询方法,SQL 预聚合按日期/provider 分组
- `LLMService` 加可选 `usage_recorder` 字段 + `caller` 参数(预留给未来按模块归因);daemon 路径(`runtime_context`)自动注入

### 修改 default 值(影响新装用户)

- **`reasoning_effort = "max"` → `""`** —— 之前默认开启 thinking 模式,DeepSeek 每次按 32K tokens 预算计费,在 discovery 评估这种打分类高频小任务上完全没必要,日花费被放大 5-10x。新装从此不再被坑;旧用户 config.toml 不会自动改,需要手工编辑或删 `config.toml` 重新走 init
- **`discovery_cron = "0 */4 * * *"` → `"0 */8 * * *"`** —— 8 小时一次发现 vs 4 小时一次,LLM 评估调用减半,UI 上换一批的"新鲜度"基本无感(pool 始终保持 600 个候选)。需要更频繁可手工调回

### 测试

- `tests/test_llm_usage.py` —— 13 个单测覆盖 pricing 数学、DB round-trip、UsageRecorder 边界(sink=None / sink 抛错 / response 无 usage 字段等)

---

## v0.3.25: discovery 成本优化(reasoning_effort + pool-aware + batch_size)（2026-05-02）

针对 daemon 运行一天烧 ¥10-20 的问题,挖到三个真实成本源,逐一压平。综合下来日花费从 ¥21 降到 ¥0.5 左右。

### 修复 / 优化

- **discovery 内容评估 batch_size 从 10 升到 30** —— 评估器已经在批量调用,但默认 batch=10 导致每个策略 30 个候选要拆 3 次 LLM 调用,~3500 tokens 的 system prompt 重复付 3 次。升到 30(配合现有 `_EVALUATE_BATCH_HARD_CAP=30`)做到 1 次评估搞定一个策略,token 总量降 54%。`max_tokens` 同步从 8192 升到 16384 给输出留 10x 头空间。回归测试 `test_evaluate_content_batch_default_size_30_uses_single_llm_call` 钉死"25 候选 = 1 个 LLM 调用"
- **pool-aware refresh limit** —— `_requested_refresh_limit` 之前永远 floor 在 30,意味着 pool 在 595/600 时还要每个策略请求 30 个候选,然后 trim_pool_to_target_count 把多余的全标 suppressed。改成按 gap 缩放:`per_strategy_target = max(5, gap * 3 // 4)`,gap 小时请求小,直接省 50-77% 的 LLM 评估调用。生产数据(13 天 11K 缓存)证明 88% 评估都是花在被立即 suppressed 的内容上的浪费

### 影响

- 单纯改 default `reasoning_effort` 已经把日花费从 ¥21 降到 ¥3.5
- 配合 `discovery_cron 8h` + pool-aware sizing + batch_size=30,steady state 日花费降到 ¥0.5
- 可用 `openbiliclaw cost` (v0.3.26 新增) 实际验证

---

## v0.3.24: 跨源事件格式统一 + soul prompt 接入 context（2026-05-02）

把 B 站 / 小红书 / 扩展点击 / 反馈等所有事件源统一到一个 `build_event()` 构造器里,所有 LLM 消费者(preference / awareness / profile_builder)都看一份带自然语言 `context` 的标准化数据。

### 新增

- **`src/openbiliclaw/sources/event_format.py`** —— `build_event()` + `format_event_context()` 单点入口,所有 producer 都走它;`SOURCE_BILIBILI / SOURCE_XIAOHONGSHU / SOURCE_WEB` 常量
- **统一 shape**: `{event_type, title, url?, context: str, metadata: {source_platform, author, ...}}`,`context` 是中文一句话描述(如 "在B 站看了《讲透历史叙事》,作者:历史实验室"),LLM 直接读不需要 schema-aware 翻译

### 修改

- 所有事件 producer 重写走 `build_event`:`_history_item_to_event`、收藏、关注、`xhs_bootstrap_notes_to_events`、`/api/events`、`/api/feedback`、`/api/recommendations/{id}/click`
- `_summarize_history` 输出新增 `contexts` / `recent_contexts` / `older_contexts`,profile_builder prompt 加 rule 13 引导 LLM 优先用 context 理解行为
- preference / awareness 分析 prompt 加 rule 8/9/5 同样引导

### 修复

- **DB context 列双重 JSON 编码 bug** —— `insert_event` 之前 unconditional 把 string 也 json.dumps 包一层引号;LLM 看到 `\"内容\"`(triple-escaped 在 prompt 里);现在 string 直存,dict/list 才编码;`MemoryManager` 默认值 `{}` → `""`

### 测试

- `tests/test_event_format.py` —— 15 个测试覆盖 producer 一致性、round-trip 不再 double-encode、legacy dict 兼容
- `tests/test_profile_builder.py` —— 4 个测试覆盖新 contexts 输出 + B 站 raw history 自动合成 fallback

---

## v0.3.23: xhs 滚动改进 + 推荐管线小修补（2026-05-02）

- xhs `bootstrap_profile` 滚动型任务改为前台 tab 执行(后台 tab 在小红书上只渲染浅层 wrapper,触发不到完整瀑布流懒加载);非滚动任务保持后台
- 滚动容器探测从固定 `document/window` 升级为优先小红书 feed/waterfall/masonry 容器,排除零高度 wrapper 和 sidebar
- 收藏/点赞分组导入对齐开源实现:`profile.user.notes[1]` 收藏、`[2]` 点赞;profile state 解析补齐 `displayTitle` / `cover.urlDefault`

---

## v0.3.22: xhs init 数据真正进画像 + UX 反馈完善（2026-05-01）

`openbiliclaw init` 端到端审计后修复多个让小红书数据基本无效的 bug。

### 修复

- **CLI 等待 8s 太短** → 拆 enqueue/collect API,enqueue 在 B 站拉数据前发出,B 站拉数据期间扩展并行跑,等需要数据时通常已经好了。env var `OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS` 默认 30s
- **`max_scroll_rounds=0` 硬编码** → 默认 3,env `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS`;`max_items_per_scope` 20 → 50
- **5 种完成状态分别打反馈** —— ok / empty / timeout / failed / skipped 都给用户看得懂的中文消息;之前完成但 0 notes 的情况静默,现在会提示"扩展跑通但没拿到 notes(可能未登录小红书 / 个人主页没有公开收藏)"

### 测试

- `tests/test_cli.py` 加 3 个回归:`test_collect_xhs_bootstrap_events_status_branches`、`test_enqueue_xhs_bootstrap_task_uses_env_overrides`、更新已有 init 集成测试

---

## v0.3.21: 装机流程 docker / PowerShell / CLI 向导对齐 v0.3.20（2026-05-01）

v0.3.20 的 UX 改动只在 Bash + AI 智能体路径生效,Docker 部署文档 / Windows PowerShell 安装器 / 直跑 CLI 向导仍是旧契约——同一个项目三种说辞。本次对齐:

- `docs/docker-deployment.md` Phase 1 主推改成 DeepSeek 默认,Ollama 加 16GB+ 硬件门槛,自建网关挪到"高级"折叠节;Phase 3 embedding 改成"3 选 1 + 默认推荐"
- `scripts/install.ps1` 镜像 install.sh 的 D4 (cookie-only 绿字 backend ready) + B4 (REUSE_FROM 警告) 修复
- `cli.py` `_LLM_MENU` 重排:DeepSeek 第一,Ollama 第六加门槛,网关第七"(高级)";`_interactive_embedding_setup` 从 4 选 1 重写成默认 Ollama bge-m3 + Gemini 取舍 + follow + 2 个高级选项

---

## v0.3.20: 装机流程 UX 修复 + Embedding 自动 fallback（2026-05-01）

针对"一句话给智能体安装"流程从普通用户视角做了若干修复：3 个真 bug（Claude/DeepSeek/OpenRouter 主模型 + 跟随 LLM 的 embedding 静默失败、`base_url` 残留、复用旧 Key 无校验）和 5 个 UX 改进（主菜单去掉自建网关 / Embedding 改成"有默认值的取舍提问" / 状态块软化 / README 加 AI Agent 前置 / Ollama 加硬件门槛说明）。

### 修复

- **B1 真 bug**：`build_embedding_service` 现在用新增的 `LLMProvider.supports_embedding` 标志做 fallback，而不是脆弱的 `hasattr(provider, "embed")`。Claude / DeepSeek / OpenRouter 标记为 `False`（前两个没 embedding API、OpenRouter 路由覆盖不全）；OpenAI / Gemini / Ollama 标记为 `True`。当主 LLM 无 embedding 能力时自动回退到 ollama → gemini → openai 链中第一个能用的，而不是返回 `None` 让推荐管线在运行时炸。同时 `OpenAIProvider` 新增 `embed()` 走 `/v1/embeddings`，为之前 OpenAI 用户没显式配 embedding 时的同样静默 None bug 补上一刀
- **B1 配套**：`agent_bootstrap.py` 在主 LLM 是 Claude / DeepSeek / OpenRouter 且用户没显式传 `--embedding-*` 时，自动写 `[llm.embedding] provider="ollama" model="bge-m3"`，并把 `bge-m3` 加进 ollama 模型预拉清单，让首次装机就把模型拉好——不再"装完了才发现 embedding 没拉模型"
- **B2 真 bug**：`set_toml_string_value` 之前只更新不删除，从自建网关（option 4）切回 OpenAI 官方（option 2）会留 `base_url` 残留，请求继续打老网关。新增 `clear_toml_string_value` / `clear_config_value`；当 `--provider openai` 显式给出且 `--llm-base-url` 未给时，自动清空 `[llm.openai] base_url`，让 SDK 回到 `https://api.openai.com/v1`，并发 `base_url_reset` 事件
- **B4 提示**：`install.sh` 复用既有 checkout 的 API Key 时摘要里加一段 ⓘ 提示，说明复用 Key 不会做校验，401 时怎么用 `REUSE_FROM=` 跳过。复用本身保持原行为（无侵入），只把"信息可见性"从隐式抬到显式

### 体验

- **D1 / D3 主菜单**：`docs/agent-install.md` Step 1 把"OpenAI 协议兼容自建网关"从平级 4 选 1 移到 "Advanced" 折叠节，主菜单只剩 3 项；新主推改成 DeepSeek（¥0.001/千 token，几乎免费），Ollama 改回"完全离线 / 不要 Key"路径并明确加上 16GB+ 内存 / CPU 推理慢的硬件门槛——不再误导新手把 Ollama 当"零摩擦"
- **D2 Embedding 改成"有默认的取舍提问"**：早期版本是"三选一让用户读 200 字解释"，本次改 v1（完全隐藏）发现霸道，最终落地 v2 ——Step 3 仍然问，但每个选项有清晰的取舍说明 + 默认推荐"不确定就回 1"：① 本地 Ollama bge-m3（默认 / 免费 / 离线）② 云端 Gemini（质量更高 / 跨语言更稳 / 需要 Key）③ 跟随主 LLM。同时保留"用户跳过 / 选项 3 + 主 LLM 是 Claude/DeepSeek/OpenRouter"时 bootstrap 的自动写 Ollama 兜底，避免运行时静默失败
- **D4 状态文案**：`install.sh` 摘要在"只缺 B 站 Cookie"这种走扩展自动同步路径的预期状态下，不再打印黄字 `partial / credentials still missing`（普通用户读成"装失败了"），改为绿字 `backend ready — waiting for browser extension to sync B站 Cookie`，并把 Next steps 改成专门的扩展安装引导
- **D5 README 前置**：`README.md` / `README_EN.md` 在"复制粘贴给 AI 智能体一键部署"上方加 📌 前置说明——你需要先有 Claude Code / Codex CLI / Cursor / Windsurf 任一；没有的用户直接看下方"自己跑一句话装机脚本"，而不是被动卡在"AI 智能体是啥"上

### 测试

- `tests/test_llm_registry.py` 新增 4 个回归测试：`test_build_embedding_service_falls_back_when_claude_is_default`（Claude → Ollama 自动回退）、`..._when_deepseek_is_default`（同上，重点验证 DeepSeek 即便继承了 OpenAIProvider.embed 也会被 `supports_embedding=False` 排除）、`..._returns_none_with_no_capable_provider`（无可用 embedding provider 时 None 而不是崩）、`test_openai_provider_supports_embedding_flag_is_set`（六个 provider 的 supports_embedding 标志正确）

### 影响范围

- 修改文件：`src/openbiliclaw/llm/{base,openai_provider,openrouter_provider,gemini_provider,registry}.py`、`scripts/{agent_bootstrap.py,install.sh}`、`docs/agent-install.md`、`README.md`、`README_EN.md`
- 行为变化：之前 OpenAI 用户没显式配 embedding 也会静默返回 None；这次 OpenAI 用户会自动用 OpenAI 的 `text-embedding-3-small`，会少量计费。如果想省 quota 显式传 `--embedding-provider ollama --embedding-model bge-m3`

---

## v0.3.19: 初始化画像混入小红书信号（2026-05-01）

本次把小红书初始化画像导入接到现有事件层：`openbiliclaw init` 会继续拉 B 站历史 / 收藏 / 关注，同时 best-effort 等待浏览器插件执行 `bootstrap_profile` 任务，把小红书收藏、点赞和小红书页面内浏览记录信号混入首轮偏好分析与画像生成。

### 新增

- 后端 `XhsTaskQueue` 支持返回 task id 的入队方法，并新增 `xhs_bootstrap_notes_to_events()`：`saved -> favorite`、`liked -> like`、`xhs_history -> view`，metadata 统一带 `source_platform="xiaohongshu"`、`note_id`、`xsec_token`、`import_source` 和 `signal_strength`
- `/api/sources/xhs/task-result` 对 `bootstrap_profile` result 会缓存 notes、保留 task result，并把转换后的事件写入 memory event layer
- 插件新增 `src/content/xhs/bootstrap.ts`，从小红书页面已渲染 state 解析 scoped notes；后台 dispatcher 识别 `bootstrap_profile`，先打开 `/explore` 找当前登录用户的 profile URL，再在同一 tab 跳到个人主页读取 `user.notes` 分组
- 收藏 / 点赞导入对齐开源实现：profile 页 `user.notes` 的 `[1]` 作为收藏、`[2]` 作为赞过；如果分组尚未加载，插件会点击 profile 页对应 tab 等待页面自己补齐 state
- profile state 解析补齐小红书 noteCard 字段：`displayTitle`、`user.nickName`、`cover.urlDefault`；受控滚动每轮会合并 state + DOM，再发送新增 partial，减少虚拟列表导致的漏采
- `bootstrap_profile` 支持显式 `max_scroll_rounds` 的受控滚动；content script 会把首批和滚动新增 notes 以 `status="partial"` 分批回传，background 等后端 `/task-result` 确认后再继续滚动，最后用 `status="ok"` 完成任务
- 滚动型 `bootstrap_profile` 会以前台 tab 打开 `/explore`，由 content script 在页面内点击导航栏“我”进入 profile；background 收到 `next_url_clicked=true` 后不再 `tabs.update(profileUrl)`，只等待同一 tab 导航完成并重新下发任务，避免直接跳 profile 触发验证码。不滚动任务仍保持后台执行；只有找不到可点击入口、只能从 state 推出 profile URL 时才回退到直接导航
- profile 二次执行前会等待小红书 React 页面真正渲染出 profile state、收藏/赞过 tab 文案或 note 卡片，避免 `tabs.onUpdated complete` 早于页面内容加载时直接返回 0 条
- 后端任务 payload 可控制滚动节奏：`scroll_wait_ms` 控制每轮滚动后的停留等待，`max_stagnant_scroll_rounds` 控制连续无新增多少轮后停止；插件端会做上下限裁剪，dispatcher 会按更长等待放宽任务 timeout
- 滚动 partial 批次现在会按 `max_items_per_scope` 的剩余名额裁剪，避免最后一轮页面一次新增多条时分批回传超过 scope 上限
- profile 滚动目标从固定 `document/window` 升级为优先探测小红书 feed / waterfall / masonry 容器，并排除零高度、`overflow-y` 非滚动式的普通 wrapper 和 `channel-list` / sidebar 这类非内容侧栏；没有内容容器时会退回到窗口级小步 `wheel` / `scrollBy`，贴近用户手动前台滚动。debug 会同时记录排名靠前的 `scroll_candidates` 和每轮 target、scrollTop、scrollHeight、clientHeight、before/after top、新增数，便于判断是否真正触发瀑布流加载
- `openbiliclaw init` 会把 XHS bootstrap 事件加入 `SoulEngine.analyze_events()` 的同批输入，并把对应 notes 追加到 `build_initial_profile()` 的 history

### 约束

- 后端仍不直接登录、爬取或调用小红书私有接口；小红书数据只来自用户浏览器里的插件
- `xhs_history` 指小红书网页自己明确暴露的浏览记录/足迹 state，不是读取 Chrome browser history；普通 `/explore` 推荐流不会再被当成浏览记录导入
- 收藏、点赞、浏览记录三个 scope 都是 best-effort：插件未连接、未登录或页面不暴露数据时，初始化继续使用 B 站数据完成；滚动也只在任务显式请求时启用

### 测试

- `tests/test_xhs_tasks.py`
- `tests/test_api_xhs_ingest.py::TestXhsTaskResults::test_xhs_bootstrap_task_result_records_events`
- `tests/test_api_xhs_ingest.py::TestXhsTaskResults::test_xhs_bootstrap_partial_results_accumulate_until_final`
- `tests/test_cli.py::test_init_includes_xhs_bootstrap_events`
- `extension/tests/xhs-task-executor.test.ts`
- `extension/tests/xhs-task-dispatcher.test.ts`

---

## v0.3.18: 把 franchise_key 升成一等字段，撤掉 v0.3.17 的标题黑名单（2026-04-30）

v0.3.17 用了**硬编码 IP 别名表 + 标题子串匹配**做 franchise 判定。社区反馈说这种黑白名单做法在长期不可持续——覆盖不全、人工维护成本高、对 LLM 编出新写法（"提瓦特 重制"、"原神 4.5 须弥"）容易漏判或误判。这次撤掉，改成**让 LLM 在内容评估阶段直接打 IP 标签**，作为 `content_cache` 的一等字段持久化。

### 撤掉的

- `src/openbiliclaw/recommendation/franchise.py`（13 个 IP 的硬编码 alias 表 + `extract_franchise()` heuristic）
- `tests/test_franchise.py`
- `_FEEDBACK_DISLIKE_FRANCHISE_PENALTY` 在 curator 里依然保留，但实现底盘换了

### 新增的：`franchise_key` 作为一等字段

**Schema**（`storage/database.py`）：

- `content_cache` 表新增 `franchise_key TEXT DEFAULT ''` 列
- `_ensure_content_cache_topic_columns()` 加 `ALTER TABLE` 迁移，老库无痛升级
- `cache_content` INSERT/UPDATE 把 `franchise_key` 纳入，`COALESCE(NULLIF(excluded.x, ''), content_cache.x)` 模式——避免被 0 值覆盖
- `get_recommendations` SELECT 多带 `c.franchise_key` 出来，给 API dedup 用
- `get_feedback_signals` SELECT 多带 `c.franchise_key`，给 curator dislike 传播用

**LLM prompt**（`llm/prompts.py`）：

`build_batch_content_evaluation_prompt` + 单 item 评估的 prompt 都加了 franchise_key 字段：

```
7. franchise_key 规则：内容如果明确属于某个具体 IP / 系列 / 作品 / 品牌，
   填它的规范名（中文优先），用于跨 topic_group 的同 IP 去重。例：
   - 「AI 重绘原神地图」「提瓦特摄影」「蒙德角色真实化」 → "原神"
   - 「星穹铁道 1.6 实战」「崩铁 角色养成」 → "崩坏:星穹铁道"
   - 「ChatGPT 工作流」「OpenAI 新模型」 → "ChatGPT"
   - 「番茄炒蛋 5 分钟教程」 → ""（一般科普 / 美食 / 通用资讯都填空字符串，不要硬凑）
   - 同一 IP 必须用相同写法。
```

LLM 已经看了 title + description + topic + style，让它顺手再标一个 IP 几乎零额外延迟。比 heuristic 准很多——「提瓦特摄影」这种隐性引用 LLM 能识别，硬编码表照不到。

**Pipeline**（`discovery/engine.py`）：

- `DiscoveredContent` 新增 `franchise_key: str = ""` field
- `to_cache_kwargs()` 把它带过去
- `_evaluate_batch` 解析 LLM 响应里的 `franchise_key`，写入 `content.franchise_key` + 评估缓存元组
- 缓存元组从 4-tuple 升到 5-tuple，老 4-tuple 兼容降级（绕过升级期 in-flight 进程崩溃）
- `evaluate_content`（单 item 版）同步处理

**Curator**（`recommendation/curator.py`）：

- `FeedbackSignals.disliked_franchises` 来源换成 `row.get("franchise_key")`（DB 里的真值），不再从 title 提
- `_feedback_adjustment` 比较 `item.franchise_key`（也是 DB 里的真值），不再调 heuristic 抽取
- 罚分常量保留 0.07（heuristic vs LLM 不影响这个值的合理性）

**API**（`api/app.py`）：

- `_cap_by_franchise()` 内联在 app.py，按 row 的 `franchise_key` 列做窗口内去重，不依赖标题
- 空 `franchise_key` 永远透传——一般内容不被限流

### 测试

- `tests/test_pool_curator.py` 新增 3 个：`disliked_franchises={"原神"}` 时，candidate `franchise_key="原神"` 扣分；`franchise_key="塞尔达传说"` 不扣；`franchise_key=""` 不扣（保护 LLM 还没标的内容）
- `tests/test_api_app.py` 新增 2 个：`_cap_by_franchise` 单元测；`/api/recommendations` 端到端——5 条 `franchise_key="原神"` 行 + 1 条 `""`，响应里只剩 2 条原神 + 番茄炒蛋

### 致谢

社区反馈「不要做黑白名单」，方向完全正确。把 franchise 升成一等字段是正解——后续还能让 `RelatedChainStrategy` 按 `franchise_key` 限制同 IP 链路深度、让 SQL 层 `trim_topic_group_overflow` 多加一个轴，全都靠这一列展开。

---

## v0.3.17: 修推荐流过度泛化 IP（一屏 5 条原神 / 提瓦特）（2026-04-30）

社区报告：点了一条「AI 重绘原神地图」之后，推荐弹窗连续出 5 条原神 / 提瓦特 / 蒙德视频。深度分析定位了 5 个层级的问题，本次先修最影响视觉体验的 3 个：

### 根因（社区分析，全部代码验证过）

1. **正反馈泛化过强**：单次 `recommendation_click` 就能让 PreferenceAnalyzer 把「原神」写入 `interests` 权重 0.6（在 `preference.json` line 348 实际命中）
2. **负反馈泛化不足**：点踩某条原神视频只记 `topic_key` 级 dislike，原神这个 IP 不会被降权（`curator.py:130-148` 验证）
3. **多样性维度太粗**：当前用 `topic_group` 限流，但同一 IP 被 LLM 拆到「游戏」「游戏动漫」「人工智能」「游戏摄影」「游戏盘点」5 个 group，绕过限流（`engine.py` 验证）
4. **`/api/recommendations` 无最终去重**：`LIMIT 20 ORDER BY DESC`，5 条原神在前则全数透传（`app.py:606`）
5. **`related_chain` 缺 IP 上限**：只按 seed_index 限流，沿原神 seed 滚 5 个邻居 = 全是原神（`related_chain.py:159` 验证）

### 本版本修复（focused subset）

新增 `src/openbiliclaw/recommendation/franchise.py`：基于标题的 heuristic franchise 提取器。预置 13 个高频 IP 的 alias 表（原神 / 星穹铁道 / 崩坏 3 / 绝区零 / 鸣潮 / 明日方舟 / 黑神话 / 塞尔达 / 我的世界 / Apex / 英雄联盟 / ChatGPT / DeepSeek），中文别名走子串匹配，英文走 `\b` 词边界（避免「lol」匹配普通笑反应）。

接入 2 个点：

1. **`/api/recommendations` 最终去重**（fix 根因 #4）：拉 40 条候选，调 `dedup_by_franchise(max_per_franchise=2)` 限同一 IP 在窗口里最多出现 2 次，再截到 20 返回
2. **Curator 的 `disliked_franchises` 集合**（fix 根因 #2）：`PoolCurator.build_context` 现在在处理 dislike 反馈时，从被踩 item 的 title 提取 franchise 加入 set；`_feedback_adjustment` 对 title 命中同 franchise 的候选扣 `_FEEDBACK_DISLIKE_FRANCHISE_PENALTY = 0.07`（比 topic 软一档，避免一条踩永久封 IP）

`storage/database.py` 的 `get_feedback_signals` 同步加 `c.title` 到查询，因为 franchise 提取需要 title。

### 没修的（留作后续）

- 根因 #1（点击 → IP 兴趣过度强化）：需要改 PreferenceAnalyzer 的 prompt 或加 TTL/最小确认次数
- 根因 #3（topic_group 多样性维度太粗）：需要在 content_cache 加 `franchise_key` 字段并由 LLM 评估时填，配合 SQL 限流
- 根因 #5（related_chain IP 上限）：同上，需要 `franchise_key` 才能在 strategy 内部限

这三个的正解都是把 franchise 上升为一等字段（DB column + LLM tag），而不是停留在 title heuristic。本次先用 heuristic 解掉用户最直接看到的问题，franchise_key 字段方案随后规划。

### 测试

- `tests/test_franchise.py`（10 个）：原神 / 提瓦特 / 蒙德 / 枫丹 / Genshin 都映射到同一 canonical key；`lol` 不会误匹配；多 franchise 时按声明顺序取首；无 franchise 的内容直接透传
- `tests/test_pool_curator.py` 新增 2 个：disliked_franchises 含「原神」时，「提瓦特摄影集锦」（不同 topic_key + 不同 up_mid）扣分；`塞尔达` 不会被殃及

### 编码乱码风险

社区还提到部分 B 站标题在数据库里有编码迹象，可能导致关键词过滤不稳。**这次没动**——但 v0.3.14 修过 memory JSON 的 GBK→UTF-8，方向类似。如果用户能复现具体的乱码字段，可以再开 issue 单独修。

### 致谢

社区诊断质量极高：5 个根因 + 5 个具体行号 + 5 个修复建议，本次修复完全按照其中可执行子集落地。

---

## v0.3.16: README 推荐顺序调整 + 多源登录前置说明（2026-04-30）

两个 README/安装文档层面的调整，没动代码：

### 1. README 后端安装方式重排：一句话装机优先，桌面包后置

之前两份 README 都把「下载后端桌面包」放第一位，「AI 一句话装机」第二位，「自己跑脚本」第三位，「Docker」混在中间。但首版桌面包未签名，会触发 macOS Gatekeeper / Windows SmartScreen，对普通用户其实最不友好。新顺序按「实际可用度」排：

1. **首选**：让 AI agent 跑 `agent-install.md`（零摩擦，agent 把 LLM/Embedding/Cookie 都问全 + 自动跑 init）
2. **或**：AI agent + Docker（v0.3.11+ 自带 Ollama embedding sidecar）
3. **或**：自己跑 `install.sh` / `install.ps1`（同一份脚本）
4. **末位**（折叠在 `<details>` 里）：下载未签名桌面包，要点「右键 → 打开」绕过 Gatekeeper

### 2. README 增加「多源登录前置」段

很多用户装好扩展后发现「为什么没有小红书内容？」——原因是后端不爬小红书，发现/详情都靠扩展在用户登录态的浏览器里跑。新增一张表，明确每个源的登录要求 + 不登录的后果：

| 源 | 登录方式 | 不登录的后果 |
|---|---|---|
| B 站 | 浏览器登录 https://www.bilibili.com（v0.3.12+ 扩展自动同步 Cookie） | 拉不到历史/收藏/关注，画像缺失，推荐降级为公共热门 |
| 小红书 | 浏览器登录 https://www.xiaohongshu.com | **完全没有小红书内容**（后端不直接抓） |
| 通用 Web 源 | 该站点正常登录 | 同上 |

并强烈推荐小红书用 CDP 模式 Chrome 复用登录态（`--remote-debugging-port=9222` + `[sources.browser] cdp_url`），避免反爬。

`docs/docker-deployment.md` 也加了同样的多源登录前置段，并把 CDP url 改成 `host.docker.internal:9222`，方便容器访问宿主机的 CDP 端口。

### 3. README_EN 同步翻译

两份 README 严格一致。
---

## v0.3.15: 一连串 Windows 装机踩坑修复 + Ollama embedding-only 不应做 chat fallback（2026-04-30）

社区反馈了一组 Windows 原生路径的坑，集中修复：

### 1. CLI 在 GBK 控制台打 emoji 直接崩

`openbiliclaw init` 开场打的「⏱」在简体中文 Windows 默认 GBK 控制台触发 `UnicodeEncodeError: 'gbk' codec can't encode character '⏱'`。修复：在 `cli.py` 顶部加 `_force_utf8_stdout_on_windows()`：

- `os.name == "nt"` 时设 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`（这俩对子进程也生效）
- 用 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 把流的 codec 换成 UTF-8 + 替换错误处理

POSIX 上完全是 no-op。`errors="replace"` 是最后一道兜底——即使有少数字符译不动，也只会显示 `?` 而不是崩溃。

### 2. install.ps1 的 `python -c '...f"{...}"...'` 在 PS 5.1 下被剥引号

PowerShell 5.1 把单引号 PS 字符串里的内嵌 `"..."` 传给 native command 时会丢内层引号。结果 `python -c 'print(f"{x}.{y}")'` 实际执行 `python -c print(fx.y)` → SyntaxError → 安装器误报「Python 3.11+ is required」。

修复：去掉 f-string 和内嵌引号，用 `print(sys.version_info[0], sys.version_info[1])`，输出 `3 11` 用空格切分。Python 端不再有 `f"..."`，PS 5.1 引号 bug 触发不到。

### 3. Bash 在 Windows 上误踩 WSL

`docs/agent-install.md` 让 AI agent 在 Windows 跑 `curl ... | bash`，但 Windows 上 `bash` 默认指向 `C:\Windows\System32\bash.exe`（WSL 启动器）。WSL 没装时报 `execvpe(/bin/bash) failed: No such file or directory`。

修复：agent-install.md 加显眼警告，告诉 AI agent 在 Windows 默认走 PowerShell；如必须用 bash，显式调 `& "C:\Program Files\Git\bin\bash.exe" -c "..."`。

### 4. 后端 Ollama embedding-only 注册不应进入 chat fallback chain

最严重的一个：用户日志里出现 `All providers failed (openai, ollama). Last error: ollama request failed: 404 page not found`。根因——`[llm.embedding] provider="ollama"` 触发 `_maybe_ollama_provider` 注册一个仅有 `bge-m3`（embedding 模型）的 Ollama provider。`LLMRegistry.register()` 不区分 chat/embedding 用途，主 provider 失败时 fallback chain 把它当成 chat provider 用，打 `/api/chat?model=llama3` → 404，还把 404 误归因「fallback 也挂了」。

修复：

- `LLMRegistry.register()` 加 `chat_capable: bool = True` 参数 + 内部 `_chat_disabled` 集合
- `_fallback_order()` 跳过 `_chat_disabled` 里的 provider
- `build_llm_registry()` 调 `_ollama_is_chat_capable(config)` 判定：用户必须在 `[llm.ollama] model` 显式给了 chat 模型，或把 ollama 设成默认/任一模块的 provider，否则视作 embedding-only，注册时传 `chat_capable=False`

回归测试：

- `tests/test_llm_registry.py::test_embedding_only_ollama_is_excluded_from_chat_fallback` —— 模拟「主 OpenAI 挂了 + Ollama 只配了 embedding」场景，断言 chat 链里**没有** ollama，断言主 provider 的错误如实抛出（不会再被「ollama 也挂了」掩盖）
- `test_ollama_with_explicit_chat_model_is_chat_capable` —— 反向验证：用户给了 `[llm.ollama] model="llama3"` 时，Ollama 仍然在 fallback 链里，符合预期

### 5. UTF-8 持久化（v0.3.14 已修，这里只是关联引用）

社区报告里同时提到 `MemoryLayer.load/save` 没指定 UTF-8 ——**已经在 v0.3.14 修了**，这里不重复。

### 致谢

非常感谢社区的细致复现 + 系统性总结。一份报告解锁四个独立 bug + 一个架构问题，PR 级质量。

---

## v0.3.14: 修 Windows GBK 默认编码导致接口 500（2026-04-30）

社区反馈在简体中文 Windows 上后端用默认 GBK locale 启动时，扩展请求 `/api/delight/pending-batch?limit=20`、`/api/activity-feed?limit=10` 等接口都会返回 500，根因是 `MemoryLayer.load()` / `save()` 在 `src/openbiliclaw/memory/manager.py` 用了不带 `encoding=` 的 `open()`：

```python
with open(self.storage_path) as f:        # ← 没指定编码
    self._data = json.load(f)             # GBK 解码 UTF-8 文件 → 报错
```

`/api/health` 是常量字符串、不读 memory 文件，所以仍然 200——bug 只在业务接口现身。

### 修复

- `MemoryLayer.load()` / `save()` 显式 `encoding="utf-8"`
- `BilibiliAuthManager.load_cookie()` / `_save_cookie()` 也补上（cookie 当前是 ASCII 不受影响，但同样不该依赖平台默认编码）
- 项目里其他文本模式 `open(...)` 全部 audit 过——`config.py` 的两处用 `"rb"` 走 `tomllib`，正确；其余都已经显式 UTF-8

### 回归测试

`tests/test_memory_manager.py::test_memory_layer_load_uses_utf8_even_when_default_locale_is_gbk`：

通过 monkeypatch `builtins.open`，让任何不带 `encoding=` 的 text-mode 调用回退到 GBK——精准模拟简体中文 Windows 的默认行为。验证：

- `MemoryLayer.load()` 仍能正确读取含中文 + emoji 的 UTF-8 文件
- `MemoryLayer.save()` 也不会触发 `UnicodeEncodeError`
- 文件最终仍是合法 UTF-8

撤回 `manager.py` 的 fix 时，这个测试会精确报出 `UnicodeDecodeError: 'gbk' codec can't decode byte 0x80`——和 prod 复现的错误一字不差。

### 致谢

非常感谢社区报告——bug 摘要、根因定位、修复思路、本地验证全跑通，整理得非常清楚，PR 级别的报告。

---

## v0.3.13: 各种安装路径都把「装扩展自动同步」放到 Cookie 步骤的首选（2026-04-30）

v0.3.12 加了扩展自动同步 Cookie，但各个安装路径的引导（向导 / 文档 / install.sh / install.ps1）都还按 F12 那套老流程在问。新用户根本不知道有更简单的路径，结果还在手动贴 Cookie。

修了 5 处：

- **`scripts/install.sh`** 状态块缺 `bilibili.cookie` 时，先打印 `(A) [recommended] Install the browser extension and let it auto-sync` 教程 + 链接，再列 `(B) F12 五步` 兜底
- **`scripts/install.ps1`** 同样的 (A)/(B) 二选一引导
- **`docs/agent-install.md` Step 4** 完全重写：明确告诉 AI agent 默认走扩展路径，不再上来就让用户 F12；如果用户选扩展，agent 不传 `--bilibili-cookie`，让 bootstrap 走 `running_with_missing_secrets` 状态，再告诉用户「装扩展，等同步」，最后再让 agent 自己跑 `openbiliclaw init`
- **`src/openbiliclaw/cli.py` 的 `_interactive_auth_setup`** 改成 2 选 1：1) 装扩展自动同步（默认，选了直接 `typer.Exit(0)`，提示之后扩展同步好再跑 `openbiliclaw init`） 2) 现场手贴
- **`docs/docker-deployment.md` / `docs/openclaw-quickstart.md`** 同步把扩展放到 Cookie 步骤的首选

效果：装扩展是默认路径，F12 是「死活不想装扩展」时的兜底。agent-install.md 给 AI agent 的指令也变了：默认不要追问 Cookie，鼓励用户装扩展，扩展同步完后续 init 就齐活了。

---

## v0.3.12: 浏览器扩展自动同步 B 站 Cookie 到后端，再也不用 F12（2026-04-30）

之前用户配 B 站 Cookie 必须自己 F12 → Network → 复制 Cookie 头 → 粘到向导里。这个体验对刚接触本项目的人极不友好，而且 Cookie 过期/刷新后还得重做。其实扩展本来就跑在 bilibili.com 上，能直接读用户的 Cookie，把这个流程自动化是天然的。

### Backend：新增 `POST /api/bilibili/cookie`

在 `src/openbiliclaw/api/app.py` 加了一个端点，接收扩展推过来的 Cookie：

1. **校验**：先用 `AuthManager.validate_cookie` 打一次 `api.bilibili.com/x/web-interface/nav`，确认 Cookie 真的处于登录状态——避免无效 Cookie 覆盖一个还在工作的旧 Cookie
2. **持久化**：写到 `data/bilibili_cookie.json`（运行时真正用的源）+ `config.toml` 的 `[bilibili].cookie`（镜像，给 `config-show` 用）
3. **热重载**：调 `RuntimeContext.rebuild_from_config` 原子换掉 BilibiliAPIClient，下一次 API 调用就用新 Cookie
4. **广播**：通过 WebSocket runtime-stream 发 `bilibili_cookie_synced` 事件，扩展 popup 可以停掉「请登录」提示

请求 model 在 `api/models.py` 新增：`BilibiliCookieIn`（`cookie`, `source`, `validate_with_bilibili`）+ `BilibiliCookieResponse`（`ok`, `authenticated`, `username`, `user_id`, `message`）。

### Extension：自动读 + 推

`extension/src/background/cookie-sync.ts` 新文件，service-worker 启动时挂上：

- **触发场景**
  - `chrome.runtime.onInstalled` / `onStartup` → 启动一次同步
  - `chrome.cookies.onChanged` 监听器（domain 收尾匹配 `bilibili.com`）→ 用户登录/登出/Cookie 刷新立即同步。debounce 2s 避免一次登录触发 6-10 次 POST
  - 每小时一次 alarm 兜底（防止 service worker 卸载期间漏掉 onChanged 事件）

- **只推有意义的 Cookie**：`SESSDATA` / `bili_jct` / `DedeUserID` 三件套缺一不发，避免后端做无谓的 nav 校验

- **只在用户登录时推**：未登录直接 `return false`，不打扰后端

`manifest.json` 加 `cookies` 权限 + 版本 0.3.1 → 0.3.2。

### 安全模型

- 后端默认绑 `127.0.0.1`，外网摸不到这个端点
- Cookie 全程在用户本机：浏览器 → service worker → localhost backend → 本地磁盘
- CORS 现状是 `*`，对 localhost 后端来说没意义（任何打到 127.0.0.1 的请求本来就来自本机）
- 用户改成 `--host 0.0.0.0` 应该自己加 auth 层（这是历史 stance，没改）

### 用户感知

- 装好扩展 → 几秒内自动同步 → 后端日志看到 `cookie_synced`，`/api/runtime-status` 返回登录态
- Cookie 过期了？扩展会在下次 `chrome.cookies.onChanged` 自动推新的，无需手动操作
- 一句话装机的 wizard 里仍保留 cookie prompt 作为兜底，给不装扩展的用户用

---

## v0.3.11: Docker 自带 Ollama embedding sidecar + CLI 向导也能自动装 Ollama（2026-04-30）

v0.3.10 把一句话装机（install.sh / install.ps1 → agent_bootstrap.py）的 Ollama 自动安装做齐了，但还有两条路径漏了：

1. **Docker 模式**：用户跑 `docker compose up -d --build` 后，embedding 段默认空着，第一次发请求才发现「咦，需要个 embedding API key 或一个 host 上跑的 Ollama」
2. **手动安装** + 直接跑 `openbiliclaw init`：CLI 向导只会检测 Ollama，没装的话提示用户去装，没启用「我帮你装」

### 1. `docker-compose.yml` 多了 `ollama` sidecar

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    # 启动时拉 bge-m3，daemon 一直跑
    # healthcheck 等到 bge-m3 就绪才报 healthy
  openbiliclaw-backend:
    depends_on: { ollama: { condition: service_healthy } }
    environment:
      OPENBILICLAW_SEED_OLLAMA_DEFAULTS: "1"
      OPENBILICLAW_OLLAMA_BASE_URL: "http://ollama:11434/v1"
      OPENBILICLAW_EMBEDDING_MODEL: "bge-m3"
volumes:
  openbiliclaw_ollama:  # bge-m3 持久化，重建容器不重拉
```

### 2. `docker_runtime.py` 启动时按 env 自动写 embedding 默认

`bootstrap_runtime_root` 复制 `config.example.toml` 到 volume 后，如果 `OPENBILICLAW_SEED_OLLAMA_DEFAULTS` 为真，就把这三个值填进去：
- `[llm.ollama] base_url = http://ollama:11434/v1`
- `[llm.embedding] provider = ollama`
- `[llm.embedding] model = bge-m3`

已有的 `config.toml` 不会被覆盖——用户改过的偏好都会保留。

效果：用户跑 `docker compose up -d --build` 后，**只需要一个 chat 模型的 API Key**，embedding 完全免费 + 离线 + 用完即走。第一次启动多花 2–4 分钟下载 bge-m3（~568MB），后续从 named volume `openbiliclaw_ollama` 直接复用。

不要 sidecar 的用户：把 `docker-compose.yml` 的 `ollama` 服务块和后端的 `OPENBILICLAW_SEED_OLLAMA_DEFAULTS` env 删掉就行。

### 3. CLI 向导（`openbiliclaw init` 直接跑）也支持自动装 Ollama

新增两个 helper：
- `_ollama_install_if_missing()`：检测 → 询问用户 → brew/winget/install.sh
- `_ollama_start_serve_background()`：后台启动 daemon，轮询 `/api/version` 等 15s

Phase 1（选 Ollama 做 chat）和 Phase 3 选项 2（选 Ollama 做 embedding）都接入了这套：用户不再需要先去外面装 Ollama，向导一条龙搞定。

---

## v0.3.10: 选 Ollama 时一句话装机自己装 Ollama + 拉模型（2026-04-30）

v0.3.6 把 Ollama 推荐成「新手默认」选项后，新问题来了：用户在向导里选了 Ollama，但实际上还得自己 `brew install ollama` / 装 Windows 安装包 / 跑 install.sh，再 `ollama pull llama3` —— 否则后端启动会卡在「Ollama not running」。这彻底违反了「一句话装机」的承诺。

`agent_bootstrap.py` 现在内置 4 阶段 Ollama 自动化：

1. **检测**：`shutil.which('ollama')` 找二进制
2. **安装**（如果没装）：
   - macOS → `brew install ollama`（没 brew 时报错并给出 https://ollama.com/download）
   - Windows → `winget install -e --id Ollama.Ollama`（自动接受 EULA；没 winget 时报错给 URL）
   - Linux → `curl -fsSL https://ollama.com/install.sh | sh`（官方脚本自带 systemd 配置）
3. **启动 daemon**（如果没在跑）：后台 spawn `ollama serve`，轮询 `/api/version` 等最多 15s
4. **拉模型**：检查 `/api/tags`，没拉的就 `ollama pull <name>`，进度流式打到 stdout

每个阶段单独发 `BootstrapResult` 事件（`ollama_installed` / `ollama_serving` / `ollama_model_pulled`），AI agent 解析 JSON 流就能精确知道卡在哪一步。最后还会发一个汇总 `ollama_ready` 事件。

触发条件：`--provider ollama` 或 `--embedding-provider ollama` 任一为真，且 `mode != docker`（Docker 模式下后端走 `host.docker.internal:11434` 找宿主 Ollama，自动装到容器内是错的）。新增 `--skip-ollama-setup` 给想自己管 Ollama 的用户兜底。

`docs/agent-install.md` 同步：Option 1（Ollama）的指引从「让用户自己装」改成「我会帮你装」，embedding 段也明确告诉 AI agent 不要让用户手动 `ollama pull bge-m3`。

---

## v0.3.9: 一句话装机适配 PowerShell 5.1（Win10/Win11 默认）（2026-04-30）

之前的 `iwr <url> | iex` 一句话在 Windows 10 / 11 上没装 PowerShell 7 的用户那里直接挂——PS 5.1 默认走 TLS 1.0/1.1，但 GitHub 现在只接受 TLS 1.2+，握手失败报「underlying connection was closed」，新手根本看不懂。

修了 4 件事：

1. **README.md / README_EN.md / docs/agent-install.md 一句话命令前缀加 TLS 1.2 设置**：
   ```powershell
   [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://...install.ps1 -UseBasicParsing | iex
   ```
   PS 7+ 用户可以省掉前缀；PS 5.1 用户必须带

2. **`scripts/install.ps1` 自身启动时也设一次 TLS 1.2**：脚本一旦开始跑，后续的 git clone / pip / uv / Invoke-WebRequest 都覆盖到了

3. **修 `?? '' ` 这个 PS 7-only 语法**：line 281 用的 null 合并操作符 PS 5.1 不支持，改成显式 `if ($null -ne $ReuseFrom) { $ReuseFrom } else { '' }`

4. **`scripts/install.ps1` 的 .EXAMPLE 注释拆成 PS 5.1 / PS 7+ 两个示例**，让用户一眼能看出哪个对应自己

`#requires -Version 5.1` 已经在文件顶部，但 PS 解析器只在脚本开始执行时检查它，对脚本下载阶段（外面那个 iwr）无能为力，所以下载阶段必须靠用户预先设好 TLS。

---

## v0.3.8: init 启动前明确告诉用户预计用时（2026-04-30）

v0.3.7 把 init 自动跑了起来，但用户看到屏幕静默几十秒就开始怀疑「是不是卡了？」。这次给 `init` 加了一段开场白，跑之前明确告诉用户：

```
⏱  这一步首次运行预计需要 2–5 分钟，请保持网络畅通别中断。
  四个阶段会依次跑：
    1/4  拉 B 站历史 / 收藏 / 关注（≈ 20–60s，看你的列表大小）
    2/4  分析偏好（LLM 调用，≈ 30–90s）
    3/4  生成灵魂画像（LLM 调用，≈ 30–60s）
    4/4  发现首轮内容池（多策略并发 + LLM 评估，≈ 1–3 分钟）
全程会打印进度，不要以为卡住了——LLM 单次响应可能就要 10–30s。
```

每个阶段的耗时区间是按官方云模型（GPT-4o-mini / Gemini Flash）+ 国内网络估的；本地 Ollama 会更慢，看用户机器。

---

## v0.3.7: 一句话装机配齐凭据后自动跑 init（2026-04-30）

v0.3.6 的人机界面虽然好了，但有个流程漏洞：用户给完凭据后，AI agent 按文档照做加上了 `--skip-init`，结果装机流程在「config 写好、健康检查通过」就停了。**用户打开扩展看不到任何东西**——画像没生成、历史没拉、首轮内容池是空的，需要再手动跑一遍 `openbiliclaw init`。这彻底违反了「一句话装机」的承诺。

### 修复内容

1. **`docs/agent-install.md` Hard Rule 第 3 条彻底反转**：原来是「Never run `openbiliclaw init` unless the user explicitly asks」，新版是「Run init by default — DO NOT pass `--skip-init`」。给 AI agent 的指令非常明确：凭据齐了就让 init 自动跑

2. **示例命令删除 `--skip-init`**：`docs/agent-install.md` 里两个示例都不再带这个 flag

3. **`agent_bootstrap.py` 的 auto-init 逻辑修了三个 bug**：
   - 之前 venv python 路径硬编码 `.venv/bin/python`（POSIX），Windows 上找不到——改成按 `os.name == "nt"` 选 `.venv/Scripts/python.exe` 或 `.venv/bin/python`
   - Docker 模式之前不跑 init——新版用 `docker exec -i openbiliclaw-backend openbiliclaw init` 在容器里跑
   - 兜底从 `python3` 改成 `sys.executable`，更可靠

4. **`install.sh` / `install.ps1` 状态块加一段说明**：
   ```
   This auto-runs 'openbiliclaw init' once credentials check out:
     - pulls your Bilibili history
     - generates the soul profile
     - runs the first content discovery pass
   Takes 2-5 minutes. Without this step the extension shows nothing.
   ```
   还在 follow-up 命令旁边加了「DO NOT add --skip-init」提示，避免 AI agent 按惯性加上这个 flag

5. **agent-install.md 增加「报告最终状态」清单**：AI agent 装完后必须告诉用户：
   - ✅ 后端已启动
   - ✅ 配置已写入
   - ✅ 初始化已完成（拉历史、生成画像、跑发现）
   - 👉 下一步：装浏览器扩展

   并提示用户 init 首次运行需 2-5 分钟，避免被以为「卡住了」

---

## v0.3.6: 装机向导从普通用户视角彻底重写（2026-04-30）

v0.3.5 的向导虽然问全了，但顺序、措辞和默认都不够友好。基于线上 AI agent 实际跑出来的提问被反馈「太差」，v0.3.6 整个人机界面重写：

### 1 — Ollama 排第一，不再把 OpenAI 当默认

之前 `default="openai"`，但 OpenAI 是收费的、要去申请 Key 才能用，对刚接触本项目的用户极不友好。v0.3.6：

- 菜单第一项是 **本地 Ollama**（免费 / 离线 / 无需 API Key），明确标注「推荐新手」
- Tip 直接告诉用户：「不想花钱、刚接触本项目，就选 1」
- 默认值改成 `1=Ollama`，回车即用

### 2 — 「OpenAI 官方」和「OpenAI 协议兼容自建网关」拆成两个菜单项

之前 `openai` 一个项要覆盖「OpenAI 公司的服务」+「Azure / vLLM / LMStudio / OneAPI / 自建网关」，从用户心智模型看完全是两件事。AI agent 也分不清要不要追问 base_url。v0.3.6 把它们拆开：

- **菜单 2 = OpenAI 官方**：只问 API Key，base_url 走 `https://api.openai.com/v1`
- **菜单 7 = OpenAI 协议兼容自建网关**：强制问 Base URL（这是唯一区分两者的字段）+ API Key + 模型名

底层都还是写到 `[llm.openai]` 段（共享 OpenAI 协议解析器），但用户和 AI agent 不再需要在心里做这个映射

### 3 — Embedding 单独成一个清晰的问题，附带解释

之前向导问完聊天模型直接接 embedding，没有明确的「这是另一件事」标识。v0.3.6 在 embedding 阶段先打印解释：

> Embedding 是和聊天模型分开的：把视频标题/简介变成向量，用于跨视频去重和相似度判定。频次很高，所以单独拎出来配。

然后才进入 4 选 1 菜单。文案也改了：选项 1 从「跟随主 provider」改成「跟随你刚才选的 LLM（最省事，默认）」

### 4 — B 站 Cookie 教用户怎么拿，不是只丢一个 prompt

之前 `_interactive_auth_setup` 只问「请输入 B 站 Cookie:」，用户看完一脸懵——Cookie 是什么？怎么拿？v0.3.6 在 prompt 之前先打印：

- **为什么需要**：拉历史训画像 + 调 B 站 API 拿视频详情
- **数据安全保证**：只存本机 `data/bilibili_cookie.json`，不上传任何地方
- **怎么获取**：浏览器 F12 → Network → 复制 cookie 请求头的 5 步流程
- **更简单的替代**：装浏览器扩展自动复用登录态

### 5 — 每个字段都有「这是干嘛的」一句话说明

例如菜单 7 选项配置时：

> 你的网关 Base URL（必填，例 http://localhost:8000/v1）
> API Key（如果网关不鉴权可留空）
> 网关上实际部署的模型名（例 meta-llama/Llama-3.1-70B）

而不是冷冰冰的 `Base URL:` / `API Key:` / `model:`

### 6 — `docs/agent-install.md` 同步重写「Asking the user the right questions」段

AI agent（Claude / Codex / Cursor / OpenClaw）跑一句话装机时会读这份 contract。新版给 agent 的指令是：

- **不要一次性把所有问题倒给用户**，分 3 步走（LLM → Embedding → Cookie）
- **解释每个东西在干嘛**（在用户语境下）
- **按选项只问该选项需要的字段**（选 Ollama 就别问 API Key；选官方厂商就别问 base_url）
- **Cookie 一定要附获取步骤**

---

## v0.3.5: 装机向导问全所有问题，不再因「openai」歧义猜错（2026-04-29）

### 4 阶段安装向导（`init` / `setup-embedding`）

之前向导只问「provider + api_key」两件事，但 `openai` 在我们这里其实是**协议家族**——Azure / vLLM / LMStudio / OneAPI / 自建网关都走这一项，base_url 和 model 不一样答案就完全不同。少问的代价是用户配完后跑不通，再被引导回来手动改 `config.toml`。v0.3.5 把向导改成：

- **Phase 1 — Provider 选择**：先打印一张 provider 协议族表，明确告诉用户 `openai` 是协议家族不是厂商
- **Phase 2 — Provider 三件套**：base_url / api_key / model，每个 provider 都带合理默认；按回车接受，不强制重输
- **Phase 3 — Embedding（4 选 1）**：跟随主 provider / 本地 Ollama bge-m3 / 自定义 OpenAI 兼容服务（vLLM / OneAPI 等）/ 指定其他已知 provider
- **Phase 4 — Per-module 覆盖（可选）**：明显标注「高级，可跳过」。给 soul / discovery / recommendation / evaluation 单独设 provider/model（典型场景：发现 / 评估走便宜模型，画像走高质量模型）

### `agent_bootstrap.py` 新增 7 个 flag，AI agent 也能问全

之前 AI agent 只能传 `--llm-api-key` + `--bilibili-cookie`，不够覆盖向导新增的字段。v0.3.5 新增：

| Flag | 用途 |
|---|---|
| `--llm-base-url` | OpenAI 兼容服务的入口 URL |
| `--llm-model` | 主 provider 的 chat 模型名 |
| `--embedding-provider` | embedding provider（空字符串 = 跟随主 provider） |
| `--embedding-model` | embedding 模型名 |
| `--embedding-base-url` | 自托管 embedding 网关的 base_url |
| `--embedding-api-key` | 自托管 embedding 网关的 API Key |
| `--module-override MODULE=PROVIDER:MODEL` | 可重复，per-module 覆盖 |

`docs/agent-install.md` 同步加了一张「最小提问表」，明确告诉 AI agent 哪些问题在哪个 flag 上传——以后不会再因为 OpenAI 兼容服务被默认成官方 OpenAI 跑挂

### 修复：测试污染开发者真实 `config.toml`

之前 4 个 `_save_*` 单元测试只 `monkeypatch.chdir(tmp_path)`，但 `_project_root()` 优先读包安装路径，结果测试值（`sk-new` / `gemini-2.0-flash-exp` / 假 `claude` 覆盖等）会写进开发者的真实 `config.toml`。v0.3.5：4 个测试改用 `monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", tmp_path)`，配合 chdir 双重保险

### 文档

- `docs/modules/cli.md`：补全 `init` 4 阶段交互式 transcript + `setup-embedding` 4 选 1 表格
- `docs/modules/config.md`：`[llm.openai]` 强调协议家族 + 新增 `[llm.<module>]` 段说明
- `docs/agent-install.md`：最小提问表 + 完整 flag 示例

---

## v0.3.4: 原生 Windows 一句话装机（2026-04-29）

### Windows 原生支持，无需 Docker / WSL2

- 新增 `scripts/install.ps1`，行为对齐 `install.sh`：克隆 / 自动升级现有 checkout / 检测 Python 3.11+ / 调用 `agent_bootstrap.py` / 输出对齐 sprintf 格式的状态块
- 用户一句话装机：
  ```powershell
  iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
  ```
- 之前 `install.sh` 第 107 行直接拒绝 `MINGW*/MSYS*/CYGWIN*` 让 Windows 用户去装 WSL2 —— 现在 PowerShell 用户走 `install.ps1` 即可

### `agent_bootstrap.py` Windows 适配

- `start_local_backend`：POSIX 用 `start_new_session=True`，Windows 用 `creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`，让 backend 真正脱离父 console 跑
- `_find_pids_on_port`：Linux/Mac 走 `lsof`；Windows 解析 `netstat -ano` 找 LISTENING PID
- `_terminate_pids`：Linux/Mac 用 `os.kill(SIGTERM/SIGKILL)`；Windows shell out 到 `taskkill /PID /T [/F]`，正确处理 Windows 进程组停止语义

### 文档

- `README.md` / `README_EN.md` 一键命令分双平台展示，加 v0.3.4 提示"无需 Docker / WSL2"
- `docs/agent-install.md` 给 AI agent 加平台检测指引：能从用户环境推断就别问
- `docs/changelog.md` 新条目（本节）

> 仅后端发版（backend-v0.3.4）。Extension 自 v0.3.1 零改动，沿用 extension-v0.3.1。

---

## v0.3.3: 修复本地 Ollama embedding 兜底实际不生效（2026-04-29）

### 关键 bug 修复

**症状**：v0.3.0 引入的本地 Ollama embedding 兜底功能在用户跑 `setup-embedding` 配好后看似生效（`config.toml` 写入 `[llm.embedding] provider="ollama"`），但实际所有 embedding 调用仍然打到 Gemini。线上日志显示 100% 的 embedding 都在 `generativelanguage.googleapis.com/v1beta/.../gemini-embedding-001:batchEmbedContents`，0% 在 `localhost:11434`。

**根因**：`_maybe_ollama_provider` 只在 `[llm.ollama] model` 或 `base_url` 有填的时候才注册 ollama provider，但 `setup-embedding` 向导只写 `[llm.embedding]`，没碰 `[llm.ollama]`。Embedding 服务找不到 ollama provider，静默回退到 default LLM provider（Gemini）。

**修复**：

- `_maybe_ollama_provider` 现在也在 `[llm.embedding].provider == "ollama"` 时自动注册 ollama，使用默认 base_url `http://localhost:11434/v1`（不影响 default chat provider）
- `_save_embedding_provider_config` 在写 `[llm.embedding]` 时如果 `[llm.ollama] base_url` 还是空，自动填 `http://localhost:11434/v1`，避免后续配置检视时 `[llm.ollama]` 全空带来的疑惑

线上 backend 重启后实测 embedding 调用立刻切到 `localhost:11434/api/embeddings` ✓

---

## v0.3.2: supergroup 合并迁离 serve 热路径（2026-04-29）

### 推荐 serve 路径零 API 调用

- `RecommendationEngine` 新增 `_supergroup_canonical_map`，由 `prewarm_supergroup_embeddings` 在每次 refresh tick 后台填充；serve()` `_merge_topic_supergroups` 退化为纯 dict lookup（零 embedding API 调用，零 pairwise 比较）
- prewarm 时重新启用 `"label | top-5 sample titles"` 的语义消歧路径——titles 用来区分 embedding 空间里看似相似的短中文 label（赛博朋克 ≈ 动漫 在裸 label 下能到 sim ≥ 0.90），但只在后台付代价
- `Database.get_topic_group_samples` 给 prewarmer 提供带 sample title 的池子摘要
- 修复早期"label-only embedding 可能误合并短 label"的质量隐患，同时不影响 popup 0.6s 响应延迟

### 工程

- `refresh.py` 把 prewarm 的 `with suppress(Exception)` 换成 `try/except + logger.exception(...)`，失败现在会进日志而不是被吞掉
- `uv.lock` 跟进 0.3.1 → 0.3.2 版本号

> 仅后端发版（backend-v0.3.2）。Extension 自 v0.3.1 零改动，沿用 extension-v0.3.1。

---

## v0.3.1: 推荐丰富度收尾 + 装机/CI 修复（2026-04-29）

### 推荐丰富度二轮治理

- **SQL 层加 per-topic_group cap**：`get_pool_candidates` 用 ROW_NUMBER 把每个 topic_group 在候选窗口里的项数封顶 3，让 270 个池子 group 中的长尾 group 真正进得到候选窗口。同时 over-fetch 由 `limit*5` 涨到 `limit*8`，给下游 balance 多留 headroom
- `_balance_pool_rows` 取消 "len(rows) ≤ limit 直接返回 SQL 顺序" 的 shortcut，改成始终 round-robin，避免 SQL 把同 topic 项目堆到候选头部
- **PoolCurator 双轴 fatigue**：原本只看 `topic_key`（细粒度），动漫杂谈/补番/解说被当成 3 个独立 topic 各自不触发 fatigue。新增 `recent_topic_groups` 维度，跨 key/group 取 max
- **fatigue 曲线陡化**：`count/len*3` → `(count^1.5)/len*5`，count=2 的扣分从 0.20 → 0.47，count=3 从 0.30 → 0.87；`topic_fatigue` 权重 0.15 → 0.25
- 实测：连续三批"换一批"的 distinct topic 数从 ~12-15 提升到 ~18-22，原 3/3 批都霸屏的 topic 现在最多 1/3 批

### 装机器 / CI 修复

- `install.sh` 检测到现有 checkout 时自动 `git fetch + git pull --ff-only`（仅当工作树干净）。之前用户重跑一句话装永远停留在旧版
- `agent_bootstrap.start_local_backend` 加端口冲突检测：旧 OBC backend 还在跑就 SIGTERM 替换；非 OBC 进程占着端口就抛 RuntimeError 让调用方报清楚
- `.github/workflows/release-extension.yml`：把无效的 `shell: node` 替换成 `bash + jq`，extension release CI 解锁
- 修了 OpenClaw proactive e2e fake 的 `get_delight_candidates` 缺失方法

### 其他

- 弹窗 probe 反馈可见性 fix（延迟 profile 重新拉取）
- speculator 已确认 speculation 在 popup 隐藏直到正式 promote
- README / 仓库 About 重新定位为通用 Agent，加 release history 表

---

## v0.3.0: 多源架构回归 + 推荐稳态重写（2026-04-28）

### 多源（multi-source）

- 重新合入此前被回滚的 Phase 0 + Phase 1 多源架构（content_id 兼容层 / SourceAdapter / SourceRecipe / BilibiliAdapter），并叠加 Phase 2 完整投产
- 新增 `xiaohongshu_adapter` 与 `web_adapter`，支持小红书与通用 web 源
- 浏览器插件加 `host_permissions: *://*.xiaohongshu.com/*`，并新增对应 content scripts (`xiaohongshu.js`)、main-world token sniffer (`xhs-token-sniffer.js`)、background `xhs-task-dispatcher`
- popup 文案/动作面、设置页、收藏夹/概览均按多源接入更新

### 推荐池多样性 / discovery 渠道平衡

- trending / explore 在评估前按 rid / domain 做 round-robin 交错，让 30 条 hard-cap 公平覆盖各分区
- 新增 `Database.trim_topic_group_overflow`，每 refresh tick 触发，把任意 `topic_group` 在 fresh pool 的占比压在 ~10% 以内（实测把 `人工智能 / related_chain` 的 207 条压回 60）
- `_build_source_replenishment_plan` 把全部缺货 source 合并到一次 `discover()` 并行 fan-out，告别"每轮一种 source"的 60s 串行
- `trim_pool_to_target_count` 加 `source_share_quotas`，三段桶（protected / negotiable_untracked / negotiable_tracked）保护 under-quota 源不被 score-only 修剪误伤
- `cache_content` UPSERT 时把 `pool_status='suppressed'` 自动复活为 `'fresh'`，让 trending 这类慢更新源能复用 B 站 ranking 不变的池子
- `_SOURCE_TARGET_SHARES` trending 比例 3 → 1，匹配实际稳态（~46）而不是 120 这个永远摸不到的目标

### 换一批（reshuffle）性能：2.6s → 0.6s

- `_merge_topic_supergroups` 的 embedding 调用 sequential await → `asyncio.gather`
- embedding cache key 由 `label | sample_titles`（每轮变 → 0% 命中）改为 `label only`（命中率 ~100%）
- popup 的 10 条 recommendation insert 由 10 次独立 commit 合并为单 transaction（消除 fsync 串行阻塞）
- 在每个 refresh tick 后 prewarm 所有 `topic_group` 的 embedding —— 新 label 进池时由后台付 API round-trip 而不是用户点击时

### 本地 embedding 兜底

- `OllamaProvider.embed()`：通过 Ollama 原生 `/api/embeddings` 拿向量，失败返空降级
- `build_embedding_service` 按 provider 选默认 model：`gemini → gemini-embedding-001`，`openai → text-embedding-3-small`，`ollama → bge-m3`
- 新 CLI 命令 `openbiliclaw setup-embedding`：探测 `localhost:11434`、流式拉 `bge-m3`、写 `[llm.embedding]` 配置；同样的 wizard 也在 `init` 末尾询问
- `install.sh` / `agent-install.md` / `README.md` / `README_EN.md` / `docs/docker-deployment.md` 全部加了"可选启用本地 Ollama embedding"指引

### 工程

- 测试：新增 trending/explore 的 interleave 回归、`trim_topic_group_overflow` 跨源 cap、`trim_pool` 三桶保护、`cache_content` 复活、Ollama embed mock + URL 处理、registry 默认 model 选择、wizard 探测/拉取/持久化共 ~20 个新测试
- 类型：所有改动通过 `mypy strict`
- 多端 lint 干净（ruff + 扩展的 tsc/node test）

---

## M8: 插件后端 API（进行中）

### 兴趣探针丰富度修正：保留大胆探索，但不再塌成同一体验轴

- **症状**：兴趣探针的方向虽然名义上跨 category，但用户体感上经常是一整批“高概念、重入口、知识解释型”方向，丰富度不够
- **根因**：speculation prompt 只强制学科 / 桥接距离分散，没有约束用户体感上的 `experience_mode` / `entry_load`；active pool 也缺少入池前的本地平衡筛选；probe push 只看 `confirmation_count`，不会避开最近已经推过的体验轴
- **修复**：
  1. `SpeculativeInterest` 新增 `experience_mode` 和 `entry_load`
  2. speculation generation 改为过采样后再本地 balanced selection，保证 active pool 至少保留轻入口和非知识解释型候选
  3. runtime push 与 OpenClaw `get_next_probe()` 共用 probe selector：验证压力相同的候选里，优先选择最近没推过的体验轴
  4. `discovery_runtime_state` 新增 `probed_axes`，与既有 `probed_domains` 一起做 probe 去重
- **测试**：新增 speculator 多样性回归、runtime / OpenClaw probe 轴去重回归，并扩展主动推送 E2E 校验 `experience_mode` / `entry_load`

### 推荐池硬上限：`pool_target_count` 从软地板升为硬天花板

- **症状**：用户反馈 popup 显示 896 条可换，远超配置 `pool_target_count=600`。排查发现 600 只作为"低于它就补货"的地板（floor），`trending` 每 3 小时 / `explore` 每 12 小时 / 事件阈值触发的 refresh 都不看总量，会越线往池子里加内容。`_run_refresh_plan` 的中途 break 条件也只在"起步低于目标"时生效
- **修复**（source-of-truth 在 `runtime/refresh.py`）：
  1. 新增 `ContinuousRefreshController._enforce_pool_cap()`：在 `refresh_if_needed` 和 `force_refresh` 入口检查 pool ≥ target 则直接返回 `{"refreshed": False, "reason": "pool_at_cap"}`，不再触发 discover。pool > target 时先调用新 DB 方法 `trim_pool_to_target_count` 把溢出部分降为 `suppressed`；每次触发都会写 INFO 日志 `enforce_pool_cap: trimmed=..., pool_available=..., target=...`，失败捕获并 `logger.exception`
  2. `_run_refresh_plan` 中途 break 条件从 `initial_pool_below_target and current_pool_count >= target` 改为 `current_pool_count >= target`：任何策略在执行过程中把池子撑到目标就立刻停
  3. 新 DB 方法 `Database.trim_pool_to_target_count(target)`：按 `relevance_score` 降序 → `last_scored_at` 降序 → 非 `explore` 优先 → `bvid` 稳定序排序，保留前 target 条，其余标 `suppressed`。只动当前 `pool_status='fresh'` 且未进入 recommendations 的条目
- **文档一致性**：`docs/modules/config.md` 的 `pool_target_count` 描述原本承诺"到达目标后不再触发新 discover"，与旧实现不符。现在行为和文档对齐
- **测试**：新增 4 个测试覆盖 `refresh_if_needed` / `force_refresh` 在 cap 时返回 `pool_at_cap`、入口触发 trim、策略中途命中 cap 就停；调整 6 个原本依赖"pool_count == target"假设的测试（降到 pool_count=20 保持原意图）；`test_refresh_controller_triggers_event_refresh_when_signal_threshold_reached` 重命名为 `_falls_back_to_full_plan_when_below_target`——原测试覆盖的"pool ≥ target 时事件阈值触发"分支现在是不可达代码

### 惊喜推荐前移到推荐页首屏

- popup `recommend` tab 新增独立的惊喜推荐首屏卡位，不再只能依赖系统通知或临时消息才能看到 delight 候选
- popup 启动、后端重连和 `init_completed` 后会主动读取 `/api/delight/pending`，runtime stream 收到新的 `delight.candidate` 也会即时刷新首屏卡
- 惊喜推荐通知点击后会打开带 `?tab=recommend&delight=<bvid>` 的插件页面，直接落到对应候选，而不是只回到通用推荐页
- 首屏惊喜卡支持 `看看 / 不感兴趣 / 聊一聊 / 稍后看` 四个动作，并会把“已打开 / 已聊过 / 先少来点”保留成本地稳定态，而不是立刻消失

### 惊喜推荐运行时修复

- delight 运行时和后台打分不再各用一套门槛：共享阈值统一到默认 `0.70`，探索开放度低时自动提高到 `0.80`，避免真实数据里分数已经够高却永远过不了 `pending` 查询
- `precompute_delight_scores()` 现在会回填“已有高分但缺 `delight_reason / delight_hook`”的 backlog，不再只处理 `delight_score = 0` 的新候选
- 后台启动时会额外跑一次 delight 预热，即使当前没有普通推荐文案要补，也会把可推送的惊喜候选准备好
- `pending delight` 只会暴露文案已就绪的候选；`suppressed` 的高分库存也允许作为惊喜推荐入口，避免被普通池限流后直接从惊喜通道里消失

### 源无关内容分类：XHS 内容入库后自动 LLM 分类

- **症状**：XHS 内容通过 `_cache_xhs_notes` 直接入库 `content_cache`，绕过了 bilibili 内容必经的 LLM 评估管线，导致 `style_key` / `topic_group` / `relevance_score` 全为空。推荐多样性机制崩溃——所有 XHS 条目共享 `"unknown"` style 和单一 `"xhs-extension-task"` topic token，一轮 10 条推荐完全被 XHS 占满
- **修复**（推荐模块为源无关统一入口）：
  1. `recommendation/engine.py::classify_pool_backlog()`：检测 pool 中 `style_key` 和 `topic_group` 都为空的条目，调用与 bilibili 同款的 LLM batch 评估 prompt 打上分类标签，结果回写 DB。分类后所有内容只有内容特征（style / topic / score），没有来源标签
  2. `api/app.py::ingest_xhs_observed_urls`：入库后 `asyncio.create_task(_classify_new_pool_items())` 触发后台分类
  3. `asyncio.Lock` 防止并发重复 LLM 调用；失败标 0.01 分防无限重试
  4. `topic_key` 自动从 `topic_group` 回填，确保 `_diversity_tokens` 有可用 token
- **DB 保护**：`cache_content()` upsert 的 `topic_key` / `topic_group` / `style_key` / `relevance_score` / `relevance_reason` 改用 `COALESCE(NULLIF(excluded.xxx, ''), existing, '')` 保护——extension 重发同一笔记不会覆盖已分类字段
- **`author_name` 字段修复**：加入 INSERT 子句 + schema 迁移，之前这个字段写了等于没写
- **`_diversity_tokens` 修复**：移除 `source_strategy` 作为 topic fallback（根因），改用作者名 + 标题中文/英文关键词
- **共享定义**：提取 `VALID_STYLE_KEYS` 到 `discovery/engine.py` 模块级，`DiscoveredContent.to_cache_kwargs()` 作为唯一的字段映射源，消除 3 处 `_VALID_STYLES` + 2 处 20-kwarg `cache_content` 展开的重复
- **空标题过滤**：extension 端 `extractNoteMetadataFromAnchor` 空标题返回 null；后端 `_cache_xhs_notes` 跳过空标题笔记。DB 历史 46 条空标题行标为 suppressed
- **测试**：新增 12 个测试（5 个 unit + 7 个 E2E multi-source diversity suite）——覆盖分类流程、重复入库保护、混排多样性、并发锁、失败重试、空标题过滤

### 兴趣探针用户确认交互

- **产品形态**：WebSocket 推送 `interest.probe` 事件 → Chrome 系统通知"阿B 想确认：你对「XX」感兴趣吗？" → 点击打开 popup Profile tab → 卡片显示猜测方向 + 具体子方向 chips → 三按钮交互：「是」「不是」「多聊聊」
- **后端**：
  - `speculator.py::user_confirm_speculation(domain)`：直接 promote 到正式兴趣
  - `speculator.py::user_reject_speculation(domain)`：30 天冷却期
  - `api/app.py::POST /api/interest-probes/respond`：接收 confirm / reject / chat，chat 转发到 dialogue 引擎
- **去重冷却**：`_PROBE_COOLDOWN_HOURS = 4`，同一 domain 4 小时内只推一次，记录在 `discovery_runtime_state["probed_domains"]`
- **推送时机修复**：`_publish_delight_if_available` 和 `_publish_interest_probe_if_available` 从 `_run_refresh_plan` 内部移到 `run_forever` 主循环——之前 pool 满时不触发 refresh plan，推送永远到不了客户端
- **插件前端**：`popup.js::renderProbeCard()` + `handleProbeResponse()` + CSS 动画；service-worker 处理 `interest.probe` 事件创建 Chrome 通知
- **CLI**：`openbiliclaw delight`（手动查看惊喜推荐候选）+ `openbiliclaw probe`（手动列出猜测方向、序号确认/拒绝）

### 架构图更新

- **discovery-architecture.html**：新增 XHS 入库 + `classify_pool_backlog` 并行通道；`pool_target_count` 300→600；refresh loop 加 `_tick_xhs_producer`
- **recommendation-architecture.html**：serve() 管道加 `classify_pool_backlog` 安全网步骤；diversity 描述更新为源无关；解耦架构图加 XHS Extension 作为第二数据源经"源无关门"入池；模块边界加 `VALID_STYLE_KEYS` 共享常量

### 修复加入 xhs 后推荐列表出现 xhs 独占轮次，丰富度塌陷

- **症状**：引入小红书内容后，一轮推荐偶尔全是 xhs 笔记——`picked summary` 出现 `{"count":10,"styles":{"unknown":10},"sources":{"xhs-extension-task":10}}`，风格 / 主题 / 平台都单一，用户每次下拉都看到同一类短视频
- **根因**：`_select_diversified_batch` 的 style cap 依赖 `_style_token` 返回的桶名，但 xhs 笔记普遍 `style_key=""`——空字符串被当成"无 style"直接跳过 style cap 检查。多个 xhs 笔记在主循环和前几档 try_fill 里都能以"空 style"身份堆到同一批次；一旦前面 cascade 没选够，最后一档无条件兜底把所有剩余项全塞进来，就凑出 10/10 xhs 独家场
- **设计原则**：用户明确要求"任何来源平等视为内容"——不走平台黑白名单，只从内容维度（topic / style）保证丰富度。平台是产地标签，不是歧视依据
- **修复**（`recommendation/engine.py::_select_diversified_batch`）：
  1. `_style_token` 把空 `style_key` 映射成 sentinel `"unknown"`——未分类内容参与 per-style cap，和有 style 分类的条目走同一套配额逻辑，不再享受空字符串"免检"
  2. 最终兜底把原本的无条件硬塞换成"broad-topic 松口径"：`fallback_broad_cap = 2 × broad_cap`。topic 才是内容丰富度的真信号——同一个 broad topic 的条目即使平台 / style 不同也会让用户感到重复。没有 topic 的条目允许通过，避免候选池薄时返回空批次
  3. 宁可返回小批次（比如 6 条 topic-diverse）也不凑满 10 条单一 topic
  4. `_build_debug_summary` 加 `platforms` 字段，日志里能直接看 bilibili / xhs 比例——仅做观测，不参与筛选
- **测试**：
  - `tests/test_recommendation_engine.py::test_monoculture_pool_capped_by_broad_topic_not_platform`——纯 xhs 同 topic 池 13 条 → 兜底 broad-topic 天花板 6 条
  - `test_content_diversity_treats_platforms_equally`——xhs + bili 混池各自 topic-rich → 两边都有代表，不再人为限量
  - `test_pure_bilibili_rich_pool_fills_batch`——纯 bilibili 富池仍填满 limit
  - `test_reshuffle_recommendations_backfills_to_requested_limit_when_style_is_dominant`——同 style 但不同 topic → backfill 到 limit
  - 全量 28 passed（recommendation_engine.py）

### MAIN-world sniffer：从 xhs 自己的 API 响应里捞 `xsec_token`

- **动机**：上一轮 token 回填修了"已经见过 token 的 note 能对齐"，但搜索页从头到尾都不走探索流的 note，历史上 `xhs_observed_urls` 根本没存过它的 token。用户点到的 `69c7a7b000000000220030c9` 就属于这类——任何途径都没捞到过 token，点击直接撞 xhs 300031 登录墙
- **思路**：xhs 的 Web 端自己会拿 token 发 `/api/sns/web/*` 请求，token 就躺在 response JSON 里。劫持 `window.fetch` / `XMLHttpRequest`，扫 response body 里所有 `(note_id, xsec_token)` 对子，回传给后端 backfill
- **难点**：content script 跑在 isolated world，`window.fetch` 不是页面的 fetch，劫持没用。必须用 MV3 的 `world: "MAIN"` 声明，让脚本和页面共享同一个 realm
- **实现**：
  1. `extension/src/main/xhs-token-sniffer.ts`（新文件）：MAIN-world 脚本，wrap `window.fetch` 和 `XMLHttpRequest.prototype.{open,send}`。`extractTokenPairs` 对任意 JSON 做深度优先扫描，认 24-hex `note_id`/`noteId`/`id` + 非空 `xsec_token`/`xsecToken`。读 body 前先 `response.clone()`，不动原始流。安装代码用 `typeof window !== "undefined"` 守护，node 测试可以只导出 `extractTokenPairs` 用
  2. `extension/manifest.json`：加第二条 `content_scripts` 给 xhs——`world: "MAIN"`、`run_at: "document_start"`，抢在 xhs 自己注入 fetch 之前挂钩
  3. `extension/src/content/xiaohongshu.ts`：isolated world 里加 `window.addEventListener("message")` bridge，收 `source: "obc-xhs-sniffer"` 的 postMessage 后缓冲 1.5s 去重，再 `chrome.runtime.sendMessage` 到 service worker
  4. `extension/src/background/service-worker.ts`：`XHS_TOKENS_OBSERVED` 消息 POST 到 `/api/sources/xhs/tokens`
  5. `api/app.py::ingest_xhs_tokens`：用 sniffed pairs 合成 `https://www.xiaohongshu.com/explore/<id>?xsec_token=<tok>` 走已有的 `_backfill_xhs_tokens` UPDATE 路径——和探索流的回填合一，不走新分支
- **隐私边界**：sniffer 不改请求、不做指纹采集、不外传任何非 `(note_id, xsec_token)` 字段。这两个值对任何登录态 xhs session 而言都是公开可读的
- **效果**：用户每逛一次 xhs 任意页面（首页 / 搜索 / 个人页），后台就从 xhs 的 API 响应里自动把可见 note 的 token 收集齐。之前存成裸 URL 的历史数据会逐步被升级成带 token 版，推荐卡点击命中 xhs 登录墙的概率随之下降
- **测试**：
  - `extension/tests/xhs-token-sniffer.test.ts`：10 例覆盖 `extractTokenPairs`——flat/nested/arrays/dedupe/camelCase/reject 非 24-hex/reject 空 token/null 入参
  - `tests/test_api_xhs_ingest.py::TestXhsTokens`：`/api/sources/xhs/tokens` 端点——token 能 backfill 到已入库的 bare cache / 空 pairs noop / malformed pair 被丢
- **手工验证**：重新 build extension + reload chrome extension 后，随便打开一条 xhs note，后台日志里能看到 `tokens upgraded=N` 出现

### 修复 xhs 笔记分享 URL 丢失 `xsec_token` 导致登录墙拦截

- **症状**：缓存的 xhs `content_url` 绝大多数是裸 `https://www.xiaohongshu.com/explore/<id>`，不带 `xsec_token=...`。DB 抽样 260 条观测 URL 里只有 15 条（全部来自 `explore` 首页）带 token，`search` 页（133 条）/ task 页（92 条）全是裸的。外链分享 / 退出登录后打开都会被 xhs 拦到登录墙
- **根因**：xhs 搜索结果页的 React 组件把 `xsec_token` 留在组件 props 里，不写入 `<a href>`；内容脚本 `passive.ts::extractXhsNoteUrl` 只能从 href 捞 token——搜索页天然捞不到。笔记详情页的权威 token 其实在 `window.location.search` 里，但原先根本没被读取
- **修复**：三处联动
  1. `api/app.py::_pick_best_xhs_url`：`_cache_xhs_notes` 写 `content_url` 前先比较——incoming 有 token 就直接用；否则回查 `xhs_observed_urls`（历史带 token 的观测）和现有 `content_cache` 行，选一个带 token 的回来。这样 xhs 先逛 explore（token 到手）再搜同一条的场景能把 token 对齐过去
  2. `api/app.py::_backfill_xhs_tokens`：`/api/sources/xhs/observed-urls` 和 `/api/sources/xhs/task-result` 收到带 token 的 URL 时，一次 UPDATE 把 `content_cache` 里同 note_id 的裸 URL 改写成带 token 版——修已存入库的历史裸 URL
  3. `extension/src/content/xiaohongshu.ts::selfNoteAnchor`：用户直接坐在笔记详情页时，合成一个"自指 anchor"塞进 collector，把 `window.location.href` 里的权威 token 上报给后端。搜索页缺的 token 在用户点进任意一条笔记时立刻补全
- **测试**：
  - `tests/test_api_xhs_ingest.py::test_tokenized_url_upgrades_existing_bare_cache_row`——裸 URL 先入库、带 token 的同 note_id 后观测，最终 DB 必须是带 token 版
  - `tests/test_api_xhs_ingest.py::test_cache_prefers_tokenized_url_from_prior_observation`——先观测带 token，再来裸 URL + `notes` payload，不准回写成裸
  - 全量 807 passed + 15 skipped

### 修复推荐列表里 xhs 笔记被当成 bilibili 视频打开（URL 错指）

- **症状**：popup 打开 xhs 推荐卡片时跳到 `https://www.bilibili.com/video/<24位 xhs 笔记 ID>`——bilibili 上根本没这条视频，点开 404。xhs 和 bilibili 内容看似"混了"
- **根因**：`storage/database.py::get_recommendations` 的 SQL 只从 `content_cache` 拉 `title/up_name/cover_url`，**没拉 `content_id`/`content_url`/`source_platform`**。下游 `/api/recommendations` 读到 `source_platform=""` 就按默认兜底成 `"bilibili"`，读到 `content_url=""` 后 popup 的 `buildContentUrl(item)` 又走 `bilibili.com/video/${bvid}` 兜底——xhs 笔记 ID 被硬塞进 bilibili 命名空间
- **修复**：`get_recommendations` SQL 补上 `c.content_id`、`c.content_url`、`c.source_platform`（`LEFT JOIN content_cache`，xhs / bilibili 通吃）。之前几轮修 `_cache_xhs_notes` / `_cache_results` 写入路径时忽略了"读回推荐"这条链路
- **测试**：`tests/test_storage.py::test_get_recommendations_joins_multi_source_fields` 守这三字段在 join 之后还能读回；全量 51 passed（storage + xhs ingest）

### 修复 xhs 笔记入库时 `source` 为空、rescore 后 `source_platform` 被覆盖成 `bilibili`

- **两个相互放大的 bug**：
  1. `api/app.py::_cache_xhs_notes` 传的是 `source_strategy=f"xhs-extension-{page_type}"`，但 `Database.cache_content` 读的是 `source` kwarg，错拼的 key 被 `kwargs.get("source", "")` 默默丢弃——xhs 所有入库笔记 `source` 列永远是 `""`
  2. `discovery/engine.py::_cache_results` 只透传 `source`，**没透传 `source_platform`/`content_id`/`content_url`/`author_name`**。cache_content 的 upsert 分支 `source_platform = excluded.source_platform` 会把 xhs 行的 `source_platform` 回写成默认值 `"bilibili"`，每次 rescore 过一遍 pool 就被覆盖一次
- **连锁现象**：DB 里出现 35 行 `source_platform='bilibili'` 但 `bvid` 是 24 字符 xhs 笔记 ID（如 `68580835000000002203315d`）、title 写着"鸡煲复刻 / 杀戮尖塔进阶"的"假 bilibili 行"
- **修复**：
  - `api/app.py:972` 把 `source_strategy=` 改成 `source=`，同时注释说明错拼 key 会被静默丢弃的坑
  - `discovery/engine.py::_cache_results` 额外透传 `source_platform`/`content_id`/`content_url`/`author_name`
  - 两条读回路径 `_backfill_candidates` 和 `recommendation/engine.py::_rows_to_discovered` 也补上从 DB 行读 `source_platform`/`content_id`/`content_url` 的逻辑（之前读回时也丢字段，导致再入库时又是默认值）
- **历史数据修正**：一次性 SQL 修 169 行——把 `source_platform='bilibili'` 且 `bvid NOT LIKE 'BV%'` 的 35 行改回 `xiaohongshu`、补齐 `content_id`/`content_url`；把所有 `source=''` 的 xhs 行标为 `xhs-extension-task`
- **测试**：
  - `tests/test_api_xhs_ingest.py::test_notes_cache_populates_source_and_platform` 守 cache_content 正确 kwarg
  - `tests/test_discovery_engine.py::test_discovery_engine_cache_results_preserves_multi_source_fields` 守 rescore 不会把 xhs 行打回 bilibili
  - 全量 804 passed（之前 802 + 本次 2）

### 修复 xhs 任务 100% 超时（丢失 EXECUTE 握手）

- **症状**：CLI `discover --source xiaohongshu` 入队后，所有 `xhs_tasks` 都在 30s 后被写成 `status=failed`、`error=timeout`，候选池没增加一条小红书笔记
- **根因**：`extension/src/background/xhs-task-dispatcher.ts` 里 `executeTask()` 只 `chrome.tabs.create` 开了后台标签，从未给内容脚本发 `XHS_TASK_EXECUTE`。内容脚本 `task-executor.ts` 的 `chrome.runtime.onMessage` 监听器永远等不到触发，30s 硬超时必然命中
- **修复**：`tabs.create` 之后注册一次 `chrome.tabs.onUpdated` 监听，页面 `status === 'complete'` 命中时 `chrome.tabs.sendMessage(tabId, {action: "XHS_TASK_EXECUTE", data: {task_id, type}})` 再立即 `removeListener`（避免 SPA 内再跳转重复发）；`sendMessage` 被拒（内容脚本缺席）时上报 `error="sendMessage_failed"` 而非静默超时；`cleanupTask()` 也清掉残留监听器
- **测试**：`extension/tests/xhs-task-dispatcher.test.ts` 新增两条 e2e（完整握手 + `sendMessage` 失败路径），手搓 `chrome.tabs` / `fetch` mock，不依赖 jsdom。8 条 dispatcher 测试全绿

### 候选池上限提到 600

- `scheduler.pool_target_count` 默认值从 `300` 提到 `600`，允许范围同步改为 `1..600`
- 运行时行为保持不变：候选池达到目标后停止 discover，掉回目标以下再触发补货，避免无谓的远端调用
- 同步更新：`SchedulerConfig` / `RuntimeRefreshController` / API models / popup 设置面板（`min/max/placeholder`）/ 文档 / 相关测试

### 修复推荐卡片封面挤压

- 侧边栏宽屏下 `116px + 1fr` 的两列 grid 叠加 `aspect-ratio: 16/10` 会让封面被拉伸、文字被挤成一条。改回 flex 纵向布局（封面全宽在上、文字在下），和早期版本体验一致
- 同时把 520px 媒体查询里的 `grid-template-columns` 覆写清掉

### 日志按大小自动轮转

- **避免失控的 7GB 日志文件**：生产中 DEBUG 级别写的 httpcore/httpx tracelog 会把 `logs/openbiliclaw.log` 撑到几个 G。切换到 `logging.handlers.RotatingFileHandler`：单文件到达 `max_file_size_mb` 立刻轮转成 `<filename>.1`，超出 `backup_count` 的老份直接丢弃
- **启动时清理历史大日志**：光换 handler 不够——`RotatingFileHandler` 不会回头处理已经超标的旧文件。`_enforce_size_budget_once` 在 `configure_logging` 开头检查一次：超过 `max_file_size_mb` 的历史文件会被重命名成 `<filename>.1`（覆盖旧 `.1`）再让 handler 从空文件写起，这正对应用户说的"超过 1G 就清理"
- **配置**：`[logging]` 新增两字段 `max_file_size_mb`（默认 1024）和 `backup_count`（默认 1）。`max_file_size_mb=0` 退回原来的 `FileHandler`（不轮转）；`backup_count<1` 时同样回退，因为 stdlib 的 RotatingFileHandler 在 `backupCount=0` 时根本不会轮转
- **磁盘占用上限**：默认配置下 `openbiliclaw.log` + `openbiliclaw.log.1` 合计不超过 ~2GB
- **测试**：`tests/test_logging_setup.py` 新增 4 个（启用轮转 / size=0 禁用 / 启动时轮转超标文件 / 小文件不动），`tests/test_config.py` 新增 2 个（默认值、TOML 解析）。全量 802 passed

### CLI `discover` 支持按来源 / 策略触发

- `openbiliclaw discover` 增加 `--source {bilibili|xiaohongshu}` / `--strategy search,trending,…` / `--limit` / `--force` 四个选项，允许单独触发某个渠道或 Bilibili 单条策略
- `--source xiaohongshu` 路径复用 `XhsTaskProducer.produce_if_due()`，`--force` 时 `min_interval_hours=0` 绕过 4 小时节流；结果直接写入 `xhs_tasks` 表交由扩展后台抓取
- `--source bilibili`（默认）走原 `ContentDiscoveryEngine.discover()`，`--strategy` 透传为 `strategies=[…]`，空值时等价于跑全策略
- 参数校验：未知 source 或未知 Bilibili 策略名直接 Typer `BadParameter` 退出码 2；xhs 路径上同时传 `--strategy` 会打印友好提示然后忽略
- 文档：`docs/modules/cli.md` 的 `openbiliclaw discover` 章节重写，给出 B 站单策略 / xhs / `--force` 三个示例

### Soul 驱动 xhs 自动发现（producer 接上）

- **后端 producer 落地**：`runtime/xhs_producer.py` 的 `XhsTaskProducer` 读取 SoulProfile → 调 LLM 改写成小红书风格关键词 → `XhsTaskQueue.enqueue("search", {keyword})`。内置最小间隔（默认 4h）防止反复抢配额；每日预算由 `XhsTaskQueue.enqueue` 强制（`sources.xiaohongshu.daily_search_budget`，默认 30）
- **LLM 关键词生成**：`sources/xhs_keyword_gen.py` 把 B 站风格的兴趣标签重写成生活化、具象、长尾、带场景的 xhs 查询（避免单字类目词）。JSON 解析走容错路径，LLM 失败即跳过该轮
- **挂接现有刷新循环**：`ContinuousRefreshController.run_forever` 每轮调用 `_tick_xhs_producer()`，和 bilibili discovery 共用同一调度器，无需额外 cron
- **闭环打通**：backend producer → `xhs_tasks` 表 → 扩展 `xhs-task-dispatcher` 轮询 → `chrome.tabs.create({active:false})` 后台执行 → `xhs/task-executor`（首屏、不滚动）回传 URLs + 元数据 → `/api/sources/xhs/task-result` 写入 `content_cache`
- **配置**：`sources.xiaohongshu.daily_search_budget` 默认从 20 提到 30（匹配产品端对 xhs 采样密度的预期）
- **测试**：`tests/test_xhs_producer.py` 新增 5 个（disabled / 预算截断 / 节流 / 空关键词 / 无画像）。全量 796 passed

### 小红书安全发现架构 (xhs-safe-discovery)

- **GPL 隔离 sidecar**：`sidecar/xhs-downloader/` 将 GPL-3.0 的 XHS-Downloader 封装在独立 Docker 容器中，通过 HTTP（`POST /xhs/detail`）与主后端通信，避免 GPL 传染。Dockerfile 固定上游 commit `5f9bd54` 确保可复现构建
- **新 XiaohongshuAdapter**：替换旧的浏览器抓取适配器，改为 HTTP 客户端调用 sidecar。并发上限 2，单 URL 失败不影响批次。后端不再直接搜索小红书（完全移除 browser-based XiaohongshuAdapter）
- **扩展被动 URL 收集**：`extension/src/content/xhs/passive.ts` 在用户自然浏览时提取视口内可见的笔记 URL（含 `xsec_token`），去重后通过 `POST /api/sources/xhs/observed-urls` 上报。**严格不自动滚动**——自动滚动是小红书风控的经典触发信号
- **任务队列**：后端 `xhs_tasks` 表 + `XhsTaskQueue` 管理搜索/创作者任务，支持每日预算限制（按类型分开计数）。扩展通过 `GET /api/sources/xhs/next-task` 轮询，`POST /api/sources/xhs/task-result` 回报结果
- **后台标签页调度器**：`extension/src/background/xhs-task-dispatcher.ts` 以 alarm 驱动轮询，`chrome.tabs.create({ active: false })` 打开后台标签页执行任务，30s 硬超时，互斥锁保证单任务飞行
- **无滚动执行器**：`extension/src/content/xhs/task-executor.ts` 用 MutationObserver + 轮询等待卡片渲染（5s 上限），提取初始视口内最多 20 个 URL，绝不调用任何滚动方法
- **创作者订阅**：`xhs_creator_subscriptions` 表 + CRUD API（`/api/sources/xhs/creators`），支持 `due_for_fetch` 查询驱动夜间调度
- **配置**：`[sources.xiaohongshu]` 新增 `sidecar_url` / `daily_search_budget` / `daily_creator_budget` / `task_interval_seconds`；`OPENBILICLAW_XHS_SIDECAR_URL` 环境变量显式覆盖（因通用 env 模式无法处理含下划线的嵌套键）
- **docker-compose**：新增 `xhs-sidecar` 服务（内部 expose 5556，healthcheck，后端 depends_on healthy），后端自动注入 sidecar URL
- **测试**：`test_xiaohongshu_adapter.py`（7 个）、`test_api_xhs_ingest.py`（5 个）、`test_xhs_tasks.py`（16 个）、`xhs-passive.test.ts`（8 个）、`xhs-task-dispatcher.test.ts`（6 个）、`xhs-task-executor.test.ts`（3 个）。全量 797 passed backend / 107 passed extension

### 多源行为采集：插件跨站 MVP

- **PlatformAdapter 接口**：`extension/src/shared/types.ts` 新增 `PlatformAdapter` 契约（`sourcePlatform` / `detectPageType` / `extractContentId` / `cardSelector` / `searchInputSelector` / `videoSelector` / `inferActionType` / `buildEventMetadata`），作为跨站适配唯一入口
- **Collector kernel 拆分**：原 `content/collector.ts` 拆成 `content/kernel.ts`（平台无关的 click / scroll / hover / search / navigation / video 观察器）+ 每个平台一个 entry（`bilibili.ts` / `xiaohongshu.ts`），构建产物变成两份 content script bundle
- **Shared 拆解**：`shared/behavior.ts` 收窄为 DOM snapshot + `createBehaviorEvent` 内核；B 站专用逻辑（`extractBvid` / 卡片选择器 / 动作关键字）下沉到 `shared/platforms/bilibili.ts`，新增 `shared/platforms/xiaohongshu.ts`（`extractNoteId` 覆盖 `/explore/{id}` / `/discovery/item/{id}` / `/search_result/{id}` 三类 URL）
- **BehaviorEvent.source_platform**：TypeScript + Pydantic 两侧都加上 `source_platform` 字段；插件上报时由 kernel 自动填（`bilibili` / `xiaohongshu`），后端 `/api/events` 把它并入 `metadata`，空串 / 留白回退 `bilibili` 保证旧扩展版本兼容
- **Manifest + 构建**：`manifest.json` 新增 `*://*.xiaohongshu.com/*` host permission 和第二条 content_script 匹配；`scripts/build.mjs` 新增 xhs entry，`dist/content/{bilibili,xiaohongshu}.js` 一起产出
- **MVP 采集范围**：小红书侧先接 snapshot / click / scroll / search；`videoSelector = null` 的适配器直接跳过视频播放器观察
- **xhs 强信号补齐**：`inferXiaohongshuActionType` 沿用与 B 站共享的中文动作词（`点赞 / 收藏 / 评论`）+ 英文回退，命中后由 `STRONG_SIGNAL_TYPES` 触发即时上报；xhs 没有"投币"，coin 分支不做匹配
- **测试**：`extension/tests/collector-helpers.test.ts` 替换为双平台单测（bilibili + xhs adapter，覆盖 like / favorite / comment 正反例），`dist-module-specifiers.test.ts` 校验两份 bundle 无 ESM 残留；后端新增 `test_events_endpoint_preserves_source_platform` 验证 xhs 事件与回退行为。全量 87/87 extension 测试 + 752 passed backend

### 跨源画像融合：source_platform_mix

- **PreferenceLayer / OnionProfile 新增 `source_platform_mix: dict[str, float]`**：持久化记录各来源的行为占比（normalized 到 1.0），序列化 / 反序列化 / Onion↔Legacy 转换全部打通
- **PreferenceAnalyzer 自动计算**：`compute_source_platform_mix()` 从批次事件的 `metadata.source_platform` 按计数归一化；`_merge_source_mix()` 用 EMA（alpha=0.3）与历史画像融合，避免一次跨站浏览就抹掉长期 B 站记录；事件缺 `source_platform` 字段时回退 `bilibili`（老数据兼容）
- **LLM 上下文自动注入**：当 `len(source_platform_mix) > 1` 时，`SoulProfile.to_llm_context()` 和 `OnionProfile.to_llm_context()` 会追加 `## 来源分布` 小节（`bilibili 60% · xiaohongshu 40%` 风格），下游推荐 / 对话 prompts 即时知道用户是多源用户
- **暂不动 LLM prompt 内的画像抽取**：preference prompt 仍不区分来源，兴趣标签未按站点打标；等多源行为量堆起来再改 prompt，避免过早优化
- **测试**：`test_preference_analyzer.py` 新增 5 个用例（mix 计数 / 空事件 / EMA 融合 / 空批次保留 prior / analyze_events 端到端），`test_soul_profile.py` 新增 7 个用例（PreferenceLayer 往返、SoulProfile / OnionProfile 多源 context、单源不渲染）。全量 765 passed + 1 skipped backend

### Phase 7 双端端到端测试

- **后端 E2E**（`tests/test_phase7_e2e.py`）：真 SQLite `Database` + 真 `MemoryManager` + Pydantic `BehaviorEventBatchIn` 校验 + 真 `PreferenceAnalyzer`（仅 LLM 本身 stub）+ 真 `OnionProfile` 序列化往返，走完混合 bilibili + xhs 批次 → 事件入库 → 偏好抽取 → 画像落盘 → LLM context 渲染的整条链路，并用第二轮纯 bilibili 批次验证 EMA 融合能保留历史 xhs 占比（0.4 → 0.28）而非抹掉
- **扩展 E2E**（`extension/tests/phase7-e2e.test.ts`）：用真 `createBehaviorEvent` + 真 `xiaohongshuAdapter` / `bilibiliAdapter` + 真 `enqueueBufferedEvent` / `shouldFlushImmediately`，覆盖 xhs 点赞 → 强信号即时 flush、多源事件在 buffer 中共存不撞 dedupe、xhs 非动作点击不触发强信号三条路径
- 全量 766 passed + 1 skipped backend / 90 passed extension

### 多源内容适配：CDP 登录态 + URL 回填

- **多源架构落地**：`sources/` 新增 `SourceAdapter` 协议 + `SourceRecipe` 数据模型，`ContentDiscoveryEngine.register_adapter()` 让 B 站之外的内容源（小红书、知乎、V2EX 等）以同一接口挂载
- **BilibiliAdapter**：把四大 B 站策略（search / trending / related_chain / explore）包装成 adapter，推进"内容源"与"策略"的解耦
- **WebSourceAdapter / XiaohongshuAdapter**：通用浏览器 + LLM 抽取通道，默认走 CDP 连 Chrome；搜索结果页已真实 E2E 验证（10/10 笔记拿到 24 位 hex note ID + 可点击 URL）
- **BrowserManager 双后端**：
  - CDP 后端：Playwright `connect_over_cdp` 复用预启动的登录 Chrome，唯一能稳定抓小红书的路径
  - agent-browser 后端：匿名回退，兼容旧行为
- **PageSnapshot + 锚点回填**：一次 CDP 往返同时拿 `innerText` 和所有 `<a>` 的 `(text, href)`。`WebSourceAdapter` 按标题模糊匹配锚点，回填 `content_url`；从 URL 路径派生 `content_id`。解决了 `innerText` 丢弃 href 导致候选无法点击的问题
- **LLM 空值修复**：`llm_extractor.py` 之前把 LLM 返回的 JSON `null` 通过 `str(None)` 变成字符串 `"None"`，污染每个空字段的真值判断。改为 `str(x or "").strip()`
- **配置**：新增 `[sources.browser]` 段（`cdp_url` + `headed`），与 `[bilibili.browser]` 独立
- **可选依赖**：`playwright>=1.40` 进入 `[browser]` optional-dependencies group，`pip install 'openbiliclaw[browser]'` 按需安装
- **测试**：`tests/test_browser_manager.py`（7 个）+ `tests/test_web_adapter.py`（4 个，含 URL 回填）+ `tests/test_xhs_e2e.py`（`@pytest.mark.integration`，真 Chrome + 真小红书）。全量 751 passed

### B 站 API 空响应容错

- 修复 `_json_object()` 对 `None` 无防护的问题：B 站 `ranking/v2` / `web-interface/view` 等接口在限流或空分区 / 删档视频场景会返回 `"data": null`，导致下游 `None.get(...)` 抛 `AttributeError` / `KeyError`
- `_json_object()` 新增 `None → {}` 短路分支，与 `_json_list()` 的 `None → []` 对称，一次性覆盖 11 处调用点（ranking / comments / search WBI / favorites cursor / video info 等）
- `get_video_info()` 将硬下标 `payload["data"]` 改为 `.get("data")`，`"data": null` 时退化为字段全默认的 `VideoInfo` 而非崩溃
- Discovery 四大策略（trending / search / explore / related_chain）的异常日志从 `logger.exception(..., exc_info=outcome)` 改为 `logger.error(..., exc_info=outcome, extra=...)`，idiomatic 之外补上 `strategy` / `error_type` / query 等结构化字段，便于观测
- 新增 2 条回归用例（`test_get_ranking_returns_empty_list_when_data_is_null` / `test_get_video_info_returns_defaults_when_data_is_null`）

### 后端 Release 自动发包

- 新增 tag 驱动的 GitHub Actions release workflow：推送 `v*` tag 后会自动构建 macOS / Windows 后端桌面包
- 后端 release 产物现已统一上传到 GitHub Releases，和浏览器插件一样走“下载附件”分发路径
- 新增版本化后端归档命名规则，例如 `OpenBiliClaw-macos-v0.1.1.zip`、`OpenBiliClaw-windows-v0.1.1.zip`
- README / 文档导航已同步补充“从 Releases 下载后端”的入口说明
- 首版桌面后端包暂未签名，文档中已明确 macOS Gatekeeper / Windows SmartScreen 可能出现的安全提示

### 插件 / 后端 Release 通道拆分

- 后端 Release workflow 现在只响应 `backend-v*` tag，并继续自动构建 macOS / Windows 桌面包
- 新增插件专用 Release workflow，插件现在通过 `extension-v*` tag 单独发布 `openbiliclaw-extension-v*.zip`
- 后端和插件各自创建自己的 GitHub Release，不再把两类附件混在同一个 release 语义里
- README、模块文档和文档导航已同步改成“插件看 `extension-v*`、后端看 `backend-v*`”的下载说明
- 历史 `v0.1.0` / `v0.1.2` 发布记录保持不动，新发布从双通道策略开始执行

### 推荐引擎解耦重构

- **新增 `serve()` 统一入口** (`recommendation/engine.py`)，所有推荐路径 (generate / reshuffle / append) 合并为一个方法，通过 `expression_mode` 参数区分实时 LLM 和预缓存两种模式
- **废弃 `discovered` 直传路径**：`generate_recommendations()` 不再接受上游传入的候选列表，引擎始终从 content_cache pool 自主拣选，与 Discovery 完全解耦
- **新增 `PoolCurator`** (`recommendation/curator.py`)，推荐侧二次评分：`rec_score = 0.4×relevance + 0.2×freshness - 0.15×topic_fatigue - 0.15×source_monotony + 0.1×serendipity ± feedback`
  - `_freshness_score()`：sigmoid 衰减，半衰期 3 天
  - `_topic_fatigue()`：近 N 条推荐中同 topic 的频率惩罚
  - `_source_monotony()`：近 N 条推荐中同 source 的频率惩罚
  - `_serendipity_bonus()`：explore 来源加分
  - `FeedbackSignals`：dislike UP → -0.20, dislike topic → -0.10, like → +0.05
- **自动补货机制**：reshuffle / append 后检查 `needs_replenishment()`，池子低于 50 时自动触发 `trigger_manual_refresh()`
- **过期淘汰**：新增 `evict_stale_pool_items()`，14 天未消费的 fresh 内容标记为 stale，每次 refresh cycle 自动清理
- **DB 新增查询**：`get_recent_recommendation_signals()` 和 `get_feedback_signals()` 为 Curator 提供评分上下文
- 新增 24 个 PoolCurator 单元测试，全部 476 个测试通过

### Discovery 评估优化框架

- **新增 `DiscoveryEvaluator`** (`eval/discovery_evaluator.py`)，支持 7 维质量评估：relevance、diversity、specificity、query_quality、explanation_quality、novelty、no_echo_chamber
- **新增 `DISCOVERY_FIELD_TO_PARAM` 归因映射**，17 个评估维度归因到 5 个 prompt（`search_queries_prompt` / `trending_rids_prompt` / `content_evaluation_prompt` / `explore_domains_prompt` / `recommendation_expression_prompt`）
- **新增 `ScenarioGenerator` + `MockBilibiliClient`** (`eval/discovery_scenario.py`)，为每个 persona 离线生成模拟 B 站内容宇宙（60 条视频 + 搜索索引 + 排行榜 + 相关图 + 行为事件），MockBilibiliClient 满足策略的 3 个 Protocol 接口
- **新增 `create_discovery_optimizer()`** (`eval/discovery_optimizer.py`)，复用 `PromptOptimizer` 核心但注入 discovery 专属参数注册表和白名单
- **新增 `run_discovery_optimizer_agent()`** (`eval/agents.py`)，发现系统专用优化 agent，可自主读文件并提出 prompt diff
- **新增自动优化脚本** (`scripts/run_discovery_auto_optimize.py`)，SGD 风格循环：persona → scenario → discover → 7 维评估 → exploit/explore → accept/rollback
- **新增人工评估脚本** (`scripts/run_discovery_eval.py`)，交互式展示发现结果和中间产物，人工打分后可触发优化
- **SearchStrategy 统一走 LLM 评估**：新增 `llm_evaluation` 和 `score_threshold` 字段，默认开启 `evaluate_content()` LLM 打分，去掉了 0.62 硬上限
- **4 个策略新增 `last_intermediates`**：运行后暴露中间产物（搜索词/分区/种子/域），供评估系统独立评估决策质量
- **`PromptOptimizer` 参数化**：`__init__` 新增 `modifiable_files` 和 `field_to_param` 可选参数，soul 和 discovery 共享 apply/commit/rollback 机制
- 新增 39 个单元测试覆盖评估器打分函数、MockClient Protocol 兼容性、ScenarioPool 缓存

### 猜测兴趣系统 (Speculative Interest Lifecycle)

- **新增 `InterestSpeculator` 引擎** (`soul/speculator.py`)，实现猜测兴趣的完整生命周期：生成 → 观测 → 转正/拒绝 → 冷却
- **高频生成**：每 10 分钟检查一次，Init 和进程启动时通过 `force_tick()` 立即触发
- **兴趣上限保护**：一级兴趣（域数）上限 15、二级兴趣（细项数）上限 60，确认兴趣 + 活跃猜测达到上限时自动跳过生成
- **LLM 驱动的兴趣猜测**：基于心理学桥接推理生成 3-5 个新兴趣方向，排除冷却期方向
- **轻量级事件观测**：每次事件 ingest 时通过关键词匹配检查是否与猜测兴趣相关，无需 LLM 调用
- **自动转正**：猜测兴趣被 3 次以上事件确认后自动提升为正式兴趣（source="speculated", weight=0.3）
- **拒绝 + 冷却**：TTL（默认 3 天）到期未确认的猜测进入 7 天冷却期，期间不再猜测该方向
- **双来源种子**：`PreferenceAnalyzer` 每次偏好分析附带产出的 `speculative_interests` 现被保留并注入 speculator 作为种子
- **Pipeline 集成**：`ingest_batch()` 自动触发观测，`tick()` 自动处理过期/转正/生成
- **Discovery 集成**：`SoulEngine.get_profile()` 附加 `_active_speculations`，`build_profile_summary()` 自动包含猜测兴趣，所有策略 LLM prompt 可见
- **API 集成**：`GET /api/profile` 返回 `speculative_interests` 字段
- **7 项配置项**：`speculation_interval_minutes / ttl_days / cooldown_days / confirmation_threshold / max_active / max_primary_interests / max_secondary_interests`
- 新增 27 个单元测试覆盖观测匹配、转正、过期冷却、兴趣上限、force_tick、间隔单位等

### SoulProfile 五层洋葱模型重构

- **新增 OnionProfile 数据结构**，将平面 SoulProfile 重构为五层嵌套模型：
  - **Core Layer**: 最稳定的核心特质（core_traits）、深层需求（deep_needs）和 MBTI 人格类型及维度强度
  - **Values Layer**: 价值观（values）和内在驱动力（motivational_drivers）
  - **Interest Layer**: 树形兴趣结构（domain → specifics），支持"国际时事 → 中东局势 / 欧洲政治"的多层级组织；同时包含 dislikes 树和 favorite_up_users 列表
  - **Role Layer**: 生活阶段（life_stage）和当前处境（current_phase）
  - **Surface Layer**: 可观察的认知风格（cognitive_style）、内容偏好（style）、使用场景（context）和探索开放度（exploration_openness）
- **MBTI 人格类型**现已内置 Core 层，包含 4 个维度的极向选择和强度评分（0.0-1.0），便于更精准的个性化推荐
- **树形兴趣结构**提升了画像表达能力，from_legacy() 自动将 v1 flat interests 转换成领域树，支持兴趣聚合与精细化表述
- **双存储方案**：soul_profile.json 存储结构化 OnionProfile v2，soul_profile.md 镜像人类可读版本，soul_changelog.md 记录每次画像更新的时间戳、触发来源、变化摘要和影响范围
- **向后兼容垫片属性**：OnionProfile 暴露 core_traits / deep_needs / motivational_drivers / values / cognitive_style / life_stage / current_phase 等垫片属性，支持现有代码无修改地访问旧接口
- **自动格式迁移**：SoulEngine 和 ProfileBuilder 透明检测 v1/v2 格式，from_dict() 自动调用 from_legacy() 迁移，已初始化的画像无缝升级到五层结构
- **兴趣树可视化**：interest.likes 和 interest.dislikes 现支持完整的 domain / specifics / weight / source 链路，便于前端展示兴趣图谱和精细反馈

### OpenClaw Adapter 集成

- 新增 `src/openbiliclaw/integrations/openclaw/`，在不改动核心推荐与学习主链的前提下，为 OpenClaw 提供独立 adapter 层
- 新增 bootstrap、DTO、operation 和协议中立 skill descriptor，可对外暴露 `sync_account / get_profile / recommend / submit_feedback / get_runtime_status`
- 新增 `src/openbiliclaw/integrations/openclaw/cli.py` JSON CLI bridge，以及仓库级 `skills/openbiliclaw-adapter/SKILL.md`，按 OpenClaw skill 目录约定提供真实可发现技能
- CLI bridge 新增 `doctor` 与 `emit-skill-descriptors`，便于调试 OpenClaw skill pack 和导出当前 skill 定义
- OpenClaw `recommend` 现已默认走快路径，不再无条件触发 runtime refresh；如需显式刷新，可使用 `--refresh-if-needed`
- 显式 refresh 超时或失败时，OpenClaw adapter 现会自动回退到缓存推荐，避免交互入口长时间挂住
- 新增 adapter / skill 单元测试，并补充集成层文档、架构说明和导航入口
- 新增 `docs/openclaw-quickstart.md`，并在 `skills/openbiliclaw-adapter/SKILL.md` 中补充 Docker 优先 / 本地兜底的部署决策、首次 `openbiliclaw init` 和 `doctor` 自检指引，方便 OpenClaw 直接落地接入

### B 站搜索 412 降噪

- `BilibiliAPIClient.search()` 现在会先从 `nav` 获取 WBI key，并切到 `/x/web-interface/wbi/search/type` 发起签名搜索请求
- 搜索请求会附带搜索页 `Referer` 和 `Origin`，更贴近浏览器真实搜索链路
- 搜索接口返回 `412 Precondition Failed` 时，客户端会记录搜索受限 warning 并保守返回空结果，不再把单次 search 失败放大成整轮 discover traceback

### discovery 兴趣锚定收口

- `ExploreStrategy` 现在允许“核心兴趣的近邻扩展”，不再把包含高权重兴趣词的方向一律视作过度相似
- 跨域外推新增硬约束：至少优先保留 2 个锚定前 5 个高权重兴趣的方向，真正不直接提及核心兴趣词的远邻方向最多保留 1 个
- `SearchStrategy` 映射搜索结果时会对高权重兴趣命中给起始锚定分，把更贴近核心喜好的 search 候选从低分池里拉出来
- `ExploreStrategy` 对没有直接兴趣锚点的远邻方向新增轻量距离惩罚，避免这类内容在排序里压过更贴近用户喜好的候选

### 推荐换一批批量与补货余量调整

- popup 的 `/api/recommendations/reshuffle` 默认批量从 `5` 提到 `10`，单次“换一批”会尽量给够 10 条；池子不够时仍允许少于 10 条
- `RecommendationEngine.reshuffle_recommendations()` 的风格多样性回填逻辑已修正，不再因为前排候选都属于同一 `style_key` 就把整批数量卡到 2~4 条
- `scheduler.pool_target_count` 默认值从 `30` 提到 `150`，后台会为 popup 连续换一批保留更大的 discovery pool 余量
- 配置现已为 `scheduler.pool_target_count` 增加 `1..300` 的范围校验；运行时单轮 discover 补货请求也会封顶在 `60`

### popup 画像分组加厚与避雷项展示

- `/api/profile-summary` 现在会返回更厚一些的画像分组：`core_traits` 最多 `6` 条、`top_interests` 最多 `8` 条，并新增 `disliked_topics`
- popup「我的画像」页新增 `最近明显会避开` 分组，不再只能看到“喜欢什么”，也能看到稳定避雷方向
- 画像生成 prompt 里 `core_traits` 的建议上限也已从 `5` 放宽到 `6`，避免前端扩容后后端长期仍只吐固定 3~5 条

### popup 画像多层认知重构

- `SoulProfile` 新增 `cognitive_style / motivational_drivers / current_phase`，画像生成现在会同时消费 `history + preference + awareness + insights`
- `personality_portrait` 的 prompt 已改成优先总结“怎么处理信息 / 在内容里长期在找什么 / 最近处于什么阶段”，兴趣 topic 只允许作为少量证据出现
- `/api/profile-summary` 与 popup 画像 tab 已同步接入这三层新字段，不再只展示一段 prose 加兴趣 chips

### explore 外推方向多样性增强

- `build_explore_domains_prompt()` 现在会明确要求跨领域外推至少覆盖 3 类不同内容方向，避免全部落在同一个抽象轴上
- prompt 新增“同一母题换皮只能保留 1 个”的约束，用来压住 `博弈论 / 桌游机制 / 策略模型` 这类近义探索方向连续灌池
- `why_it_might_resonate` 现在被要求先回到用户的认知需求和信息处理偏好，再解释题材为什么可能打动他

### explore 单簇灌池与补货状态语义修正

- runtime refresh 现在会在补货后温和压一轮 `explore` 高风险子簇的过量 fresh 候选，优先处理制造 / 工艺 / 材料、博弈 / 桌游 / 机制这类容易连续刷屏的相邻方向
- discovery runtime state 新增 `last_discovered_count`，补货状态不再只用“可立即换库存净增”来表达本轮 refresh 的结果
- popup pool summary 现在会区分“正在补货”“这轮找到了内容但可换库存没变”“刚补进 N 条”，不再把 refresh 进行中和上一轮净新增为 0 混成同一句

### popup 推荐头部信息面板整理

- 推荐 tab 头部已从“标题 + 按钮 + 三行池子状态”改成单张轻量信息卡，主操作和状态层级更清楚
- 候选池摘要现在拆成 `当前可换 / 最近补进 / 现在在忙` 三块语义面板，不再像一段连续日志
- 点击 `换一批` 时，进行中的文案会直接进入“现在在忙”状态块，避免按钮旁边再漂一条独立提示导致布局抖动
- 推荐 tab 头部现已进一步收成紧凑双层结构：标题行 + 状态 chips 行，明显减少首屏占用，让推荐内容更早露出
- pool summary 文案同步收短成 chip 友好的形式，例如 `还有 151 条可换 / 刚补进 6 条 / 这会儿先不补货`

### popup For You 编辑式重排

- 推荐 tab 的 `For You` 区块进一步改成内容优先的编辑式布局，头部导语、池子摘要和首张内容卡的层级明显分开
- 推荐卡片改成更清晰的纵向信息节奏：上层是封面和主题标签，中层是标题与推荐理由，下层是 UP 主信息和反馈操作
- 视觉上收敛了过重的装饰层，首屏更像内容推荐流，而不是状态面板拼装

### discovery pool 预生成推荐文案

- discovery pool 现在会在内容入池后异步批量预生成 `expression` 和 `topic_label`，`reshuffle/append` 不再现场兜底生成整批统一文案
- popup 推荐卡片改成“有预生成文案就展示，没生成好就先隐藏”，不再把空值补成固定占位文案
- runtime refresh 在补货后会顺手触发这轮 pool copy 预生成，保证“换一批”继续保持秒级响应

### popup 推荐自动续页

- 新增 `POST /api/recommendations/append`，popup 推荐 tab 滚到底时会继续从 discovery pool 追加下一批 10 条
- 自动续页会把当前已展示的 `bvid` 传给后端排除，避免追加时和当前列表重复
- `换一批` 仍保留为整组重开；自动续页只负责在当前列表底部继续往下接内容
- 修复了续页新卡片封面偶发空白的问题：popup API 现在会统一规范化 `cover_url`，同时封面不再依赖会误伤内部滚动容器的原生 lazy loading

### SQLite 修复与防损坏加固

- 新增 `openbiliclaw db-repair`，会先检查完整性、拒绝带占用修复、备份 `db/db-wal`，再尝试恢复到 repaired 副本并切换正式库
- `openbiliclaw start` 现在会在启动前检查数据库健康度；检测到损坏时会直接阻止启动，并提示先执行 `db-repair`
- 运行时增加默认 24 小时冷备份策略，自动把健康数据库备份到 `data/backups/`，并按“最近 7 份日备 + 4 份周备”轮转
- `Database` 的推荐更新写路径现已统一走带锁重试的写入口，减少 `database is locked` 后局部裸写带来的风险
- CLI / API 的高流量路径开始共享同一个 SQLite 实例，避免同进程重复初始化多份连接

### Docker 一键后端部署支持

- 新增 `Dockerfile`、`.dockerignore` 和单服务 `docker-compose.yml`，支持 `docker compose up -d` 启动后端
- CLI `start` 现在支持 `--host` / `--port`，同时新增 `serve-api` 作为容器友好的显式启动入口
- 默认 compose 现已改为 Docker named volumes，配置、数据、日志都与宿主机项目目录隔离
- 修复安装包运行时的根目录解析问题，容器内现在会正确读取 `/app/runtime/config.toml` 并把数据写入 `/app/runtime/data`
- 容器启动时现在会自动探测宿主机 Clash HTTP 代理；默认探测 `host.docker.internal:7897`，可达则透传代理，不可达则继续直连
- `openbiliclaw init` 现在支持交互式引导：Docker 用户首次执行时可直接补齐默认 provider、API Key 和 B 站 Cookie，然后继续完成初始化
- 容器内通过 `docker exec openbiliclaw ...` 执行任意 CLI 命令时，也会重复这层 runtime/bootstrap 逻辑，避免只有主进程有代理、交互命令却直连失败
- discovery 内部已经改为保守受控并发：Search / Trending / Related / Explore 会共享较小的 B 站请求与 LLM 评分并发上限，减少首轮 init/discover 的明显串行耗时
- `openbiliclaw init` 的 discover 阶段现在会按 `search + related_chain -> trending -> explore` 分阶段补货，尽量把首轮 fresh 候选池补到至少 `100` 条，降低第一次 `recommend` 直接空池子的概率
- `openbiliclaw init` 运行时会同步打印每个补货阶段的当前池子进度和本轮请求上限，首轮等待时不再只有一个静态“发现内容”标题
- 修复 `DiscoveryConcurrencyController` 在多次 `asyncio.run(...)` 间复用 semaphore 的跨事件循环问题，Docker/CLI 首轮分阶段补货不再在第二阶段报 `Semaphore ... is bound to a different event loop`

### discovery pool 目标扩容

- `scheduler.pool_target_count` 默认值现已从 `150` 提到 `300`，运行时会持续以 300 条 fresh 候选为目标补货
- `openbiliclaw init` 的首轮补货目标保持保守分层策略，但保底值已从 `50` 提到 `100`
- 现有护栏保持不变：`pool_target_count` 仍限制在 `1..300`，单轮 refresh discover 回填仍封顶 `60`

### 同批推荐多样性约束

- `generate_recommendations()` 和 `reshuffle_recommendations()` 现在不会只按分数直取前 N
- 同一批里会对重复 `tags/topic` 做软限流，尽量避免连续出现太多同一方向的内容
- 候选不足时仍会回填高分内容，保证多样性约束不会把推荐数量卡没

### topic_key 多样性强化

- `content_cache` 现在会持久化稳定 `topic_key`，推荐层不再只靠空 `tags` 猜 topic
- `SearchStrategy` 会把 query 派生的 `topic_key` 写入候选，`RelatedChainStrategy` 会把 seed chain 继承成 `topic_key`
- `generate_recommendations()` 和 `reshuffle_recommendations()` 现在优先按 `topic_key` 分桶，每个 topic 先出 1 条，再按分数回填
- `ContentDiscoveryEngine` 在写入 discovery pool 前会先压一轮同 topic 重复项，减少单一相关推荐链把池子灌满的情况

### 风格多样性与快速文案增强

- discovery 入池时会按标题、描述和基础理由轻规则补 `style_key`，区分 `deep_dive / news_brief / game_strategy / practical_guide / story_doc / visual_showcase / light_chat`
- `reshuffle_recommendations()` 现在会同时约束 `topic_key + style_key`，避免一批里虽然 topic 不同，但全是同一种“很干很学术”的内容风格
- 快速换一批的 fallback 文案不再直接裸用 `relevance_reason`，而会按 `style_key` 生成更自然的老B友短句

### 候选窗口来源交错与 10 条批次硬上限

- `get_pool_candidates()` 现在会对 discovery pool 做来源交错取样，优先把 `search / trending / related_chain / explore` 混进同一候选窗口，而不是先吐出一屏 `explore`
- `reshuffle_recommendations()` 现在会同时对 `topic_key + style_key + source` 加硬上限；10 条一批时单一来源最多 3 条，小批次也会优先保留不同来源，减少“换一批还是同一个味”的情况

### 来源优先补齐与风格误判修正

- discovery 与 recommendation 的多样性选择现在会优先补齐不同 `source`，再施加 `style` 上限，避免 `trending/search` 还没出场就被重复的 `explore` 候选挤掉
- `infer_style_key()` 补强了芯片/显微镜/纳米/理论/哲学等硬核解析词，以及“全过程 / 制造过程 / 工艺难度”等纪录片/工业流程词，减少大量硬内容被误判成 `light_chat`
- 推荐候选与选中摘要日志现在更容易对应“来源是否真的被补齐”，便于继续定位池子上游偏移问题

### 候选池按来源缺口补货

- runtime refresh 在池子低于 `pool_target_count` 时，不再一视同仁地把所有策略各跑一轮，而是会先统计 `search / related_chain / trending / explore` 当前池子占比
- 补货现在会优先补足缺口更大的来源；例如 `trending` 为 0、`explore` 已经超标时，会先补 `search/related` 和 `trending`，而不会继续加码 `explore`
- `database` 新增按来源统计 fresh pool 的能力，候选池状态现在不仅看总量，也看来源结构是否失衡

### 池子已满时的状态文案修正

- popup 候选池摘要现在会在 `pool_available_count >= pool_target_count` 且最近没有新增入池时，显示“这会儿先不补货，池子里已经够你换了”
- 不再用“刚补进 0 条新的”误导用户以为后端没在工作

### popup 动态状态卡与活动历史

- popup 底部提示区现在升级为两行可展开动态卡，默认显示“现在在忙什么 / 最近一次关键变化”
- 新增 `/api/activity-feed`，聚合认知更新、反馈记录、换一批和候选池补货等最近活动
- 点 `更多` 后会展开最近历史，不再只能看单条瞬时提示

### 画像认知卡片历史分页

- `/api/profile-summary` 现在会返回结构化认知卡片分页结果，新增 `has_more_cognition_updates / next_cognition_cursor`，popup 可继续拉取更早的认知变化
- popup「阿B 最近新记住了什么」升级为可展开卡片：默认看一句总结，展开后能看到“这对画像的影响 / 为什么这么判断 / 这次依据”
- 评论型认知卡片现在会带上对应内容标题，避免只看到“这个很好看”却不知道是在评价哪条内容
- 画像 tab 首屏先展示 3 条认知变化，并支持滚动自动续页；底部保留“加载更多 / 重试加载”按钮作为兜底

### 认知卡片上下文与展开状态澄清

- 认知卡片默认态现在固定显示“结论 + 上下文 + 状态提示”，例如 `来自：《某条内容》`、`来自最近这轮聊天：…`、`基于最近主题：…`
- `/api/profile-summary` 新增 `context_line / source_label / expand_hint`，前端不再把 `画像观察` 这类泛标签当作默认上下文
- popup 会显式区分 `展开 / 收起 / 仅结论`，不可展开卡片不再做成像按钮的样子；聚合判断拿不到可信对象时会保守回退为“基于最近几条相关内容”

### 推荐评论发送状态可见化

- 推荐卡片里的 `说说原因 -> 发出去` 现在会立刻切到 `发送中...`，成功后显示 `已发出` 并回写本地状态文案
- 请求失败时按钮会恢复可点，卡片本地会直接提示“这句还没发出去，可以再试一次”，不再只能靠底部横条猜测

### 账户侧定时同步 — `runtime/m115-account-sync`

- 本地后端运行时新增低频账户同步链路，会定期拉取 `history / favorites / following`
- 新数据会统一转成 `view / favorite / follow` 事件，再复用 `SoulEngine.analyze_events()` 更新偏好与画像
- 新增 `account_sync_state.json` 保存历史游标、收藏/关注签名和最近同步错误
- `runtime-status` 新增 `last_account_sync_at` / `last_account_sync_error`，便于 popup 或诊断页展示账户同步状态

### 聊天即时认知阈值放宽 — `runtime/m114-chat-cognition-threshold`

- popup/CLI 聊天现在对 `interest / value / goal / dislike` 这类单条中高置信信号更敏感，会更早进入「阿B 最近新记住了什么」
- 偏好重分析和画像重建仍保留原有重复出现/累计阈值，不会因为一句随口聊天就改动长期画像

### 单条强聊天即时认知更新 — `runtime/m113-immediate-chat-cognition`

- 单条高置信度聊天信号现在也可即时写入轻量 cognition update，供 popup「阿B 最近新记住了什么」优先展示
- 大规模偏好重分析和画像重建仍保留原有候选累计阈值，不会因为一次聊天就重写整张画像

### popup 画像摘要即时刷新

- side panel 在聊天、`多来点`、`少来点`、`说说原因` 成功后，会强制重拉 `/api/profile-summary`
- 修复“阿B 最近新记住了什么”只在首次打开画像 tab 时加载，之后不跟着新反馈/新聊天更新的问题

### 强反馈即时认知更新 — `runtime/m112-immediate-cognition-feedback`

- 单条 `dislike` / `comment` 反馈现在会即时写入轻量 cognition update，供 popup「阿B 最近新记住了什么」立刻展示
- 偏好重分析和画像重建仍保持现有 `>= 3` 条反馈阈值，不会因为一次反馈就重写整张画像

### 运行时实时状态流 — `runtime/m111-runtime-stream`

- 新增 `/api/runtime-stream` websocket，popup 打开期间可持续接收后端运行阶段事件
- 刷新器现在会广播“开始补候选 / 当前策略 / 刚补进几条新的 / 这批先换好了 / 补货失败”等状态
- popup 底部提示横条和池子摘要会随着事件流即时更新，不再只显示静态数字

### Popup 底部提示增强 — `extension/m110-hint-banner`

- popup 底部提示区从淡灰说明文案升级为带状态点的横条提示，成功 / 提示 / 错误三种状态现在更容易区分
- `喜欢 / 不喜欢 / 写一句 / 换一批 / 聊天发送` 等关键动作都会同步切换提示语气，减少“操作成功了但不明显”的问题

### 候选池容量与状态展示 — `runtime/m107-pool-status-capacity`

- `scheduler.pool_target_count` 现在可以控制 discovery pool 期望保有的可换候选数量，后台刷新器会持续补货直到池子接近目标
- `runtime-status` 新增 `pool_available_count`、`pool_target_count`、`last_replenished_count`、`recent_pool_topics`
- popup 推荐 tab 会展示“当前池子里还有多少条可换 / 刚补进多少条新的 / 最近主要在补什么”
- discovery pool 查询现在会排除已经进入 `recommendations` 的内容，减少“换一批还是老面孔”的情况

### 推荐卡片封面展示 — `extension/m108-cover-cards`

- `/api/recommendations` 与 `/api/recommendations/reshuffle` 现在都会返回 `cover_url`
- popup 推荐卡片升级为“封面 + 文本信息 + 操作区”结构，换一批时可以直接先看封面再决定点不点
- 封面缺失或加载失败时会回退到占位态，不影响换一批、打开视频和反馈流程

### 封面地址规范化修复 — `extension/m109-cover-normalization`

- popup 现在会把 `//i*.hdslb.com/...` 和 `http://i*.hdslb.com/...` 统一规范成 `https://...`
- 修复了部分推荐卡片因为协议相对地址或不安全地址导致封面加载失败的问题

### 插件侧边栏模式 — `extension-sidepanel`

- 扩展入口从 `action.default_popup` 切到 `side_panel.default_path`，点击扩展图标时会优先打开侧边栏
- service worker 新增统一的扩展 UI 打开链，通知和认知提醒也会优先把用户带回插件侧边栏上下文
- 现有 `popup/` 页面继续复用，但布局已从固定小弹窗改成更适合侧边栏浏览的长页面容器

### 候选池即时换一批 — `runtime/m106-pool-reshuffle`

- popup 推荐 tab 现已从“立即刷新完整补货”改成“换一批”，直接调用 `/api/recommendations/reshuffle`
- `content_cache` 现在作为真正的 discovery pool 使用，候选项新增 `pool_status`、`recommended_at`、`feedback_type`、`feedback_at`
- `RecommendationEngine.reshuffle_recommendations()` 会直接从池子里拣一批 `fresh` 候选，不等待完整 discover 完成
- popup 展示文案会优先使用候选池自带的 `relevance_reason`，朋友式 `expression` 成为增强层，不再阻塞即时换片

### Popup 手动刷新推荐 — `extension/m86-manual-refresh`

- popup 推荐 tab 新增“立即刷新”按钮，点击后会调用 `/api/recommendations/refresh` 触发一次完整补货
- 刷新期间按钮会进入“正在补货…”状态，成功后立即重拉运行状态和推荐列表
- 刷新失败时保留当前推荐，不清空内容，只给出轻量错误提示
- 后续修正：手动刷新现在走 `force_refresh()`，不会再因为 `below_threshold` 被短路

### 候选供给升级 — `candidate-supply`

- `ContentDiscoveryEngine` 现在采用“主发现 + backfill”两阶段流程：主候选不足时会扩搜索、放宽高精度策略阈值，并从历史缓存补齐到目标上限
- `content_cache` 新增 `relevance_score`、`relevance_reason`、`candidate_tier`，缓存候选与实时发现候选终于共享同一套质量信号
- `RecommendationEngine` 和 `Database.get_unrecommended_content()` 现已统一按 `candidate_tier -> relevance_score -> last_scored_at -> view_count` 排序，避免缓存回读退化成只看播放量

### Popup 手动刷新异步化 — `runtime/m105-manual-refresh-async`

- `/api/recommendations/refresh` 现在只负责触发后台手动补货任务，立即返回接受结果
- `runtime-status` 新增 `manual_refresh_state` 和 `manual_refresh_message`，popup 会轮询后台状态，而不是同步等待整轮补货
- 手动刷新期间 popup 继续保留当前推荐列表，等后台补货完成后再统一重拉推荐

### Gemini 可选依赖导入修复 — `fix/gemini-optional-import`

- `google-genai` 缺失时，`openbiliclaw.llm` 和 `openbiliclaw.llm.registry` 现在仍可正常导入，不再因为 Gemini 顶层依赖阻塞整个测试收集
- 只有真正实例化 `GeminiProvider` 时才会抛出明确错误，提示安装 `google-genai`
- Gemini 功能测试改为“有 SDK 才跑功能，无 SDK 则验证友好降级”，恢复主线测试可运行性

### 关键认知变化提醒 — `runtime/m104-cognition-notify`

- 新增 `cognition_updates.json`，记录关键认知变化、来源、置信度和已通知状态
- 反馈刷新与聊天学习链路现在会生成 `interest_added`、`dislike_added`、`profile_shift` 三类认知变化
- 新增 `/api/cognition-updates/pending` 与 `/api/cognition-updates/seen`，供插件拉取并确认认知提醒
- service worker 现在会在推荐通知之后检查认知变化通知；popup “我的画像” tab 会展示“阿B 最近新记住了什么”

### 持续候选池刷新与通知 — `runtime/m103-continuous-refresh-notify`

- 新增 `ContinuousRefreshController`，在本地 API 运行时按“事件触发 + 定时保底”持续刷新候选池，并分层调度 Search/Related、Trending、Explore 策略
- 新增 `discovery_runtime.json`，持久化最近刷新时间、最近处理事件 ID 和最近通知时间
- `content_cache` 新增 `last_scored_at`、`notification_sent`、`notified_at`，用于候选保鲜和通知去重
- 新增 `/api/runtime-status` 与 `/api/notifications/pending`、`/api/notifications/sent`，popup 和 service worker 可分别读取运行状态、拉取待发通知并确认送达
- popup 现在会区分“未初始化 / 正在补货 / 推荐可用”三态，service worker 会对高置信且未通知的推荐触发浏览器通知并回写已发送状态

### Gemini Provider 支持 — `gemini-provider`

- 新增 `GeminiProvider`，按 Gemini 官方 quickstart 接入 `google-genai` SDK，支持统一的空响应校验、错误归一化和 usage 标准化
- 配置层新增 `[llm.gemini]`，支持 `api_key` 与 `model`，默认模型为 `gemini-2.5-flash`
- `LLMRegistry` 现在可以自动注册 `gemini`，并在 `config.toml` 缺 key 时回退读取 `GOOGLE_API_KEY` / `GEMINI_API_KEY`
### B站动态语气优化 — `tone/m94-bilibili-tone`

- 新增 `ToneProfile` 派生层，从画像、偏好摘要和近期反馈推断 `density / warmth / playfulness / directness`
- 推荐表达、画像总结和聊天 prompt 统一接入这层语气系统，基础风格改为“老B友”，但会随用户理解逐步细调
- 推荐理由减少算法解释腔，画像减少心理报告感，聊天保留追问能力但更像懂 B 站语境的老朋友

### OpenRouter Provider 支持 — `llm/openrouter-provider`

- 新增 `OpenRouterProvider`，通过 OpenAI-compatible 调用链接入统一的超时、重试、错误归一化和 JSON mode
- 配置层新增 `[llm.openrouter]`，支持 `api_key`、`model`、`base_url` 以及可选请求头 `http_referer` / `x_title`
- `LLMRegistry` 现在可以自动注册 `openrouter`，并支持把它设为默认 provider

### Popup UI 刷新 — `extension/popup-ui-refresh`

- popup 从深色工具面板重构为亮色三 tab 发现页，顶部采用 hero + inline 状态徽标，整体更贴近 B 站内容产品气质
- 推荐卡片、画像卡和聊天区统一为同一套浅色卡片系统，推荐内容成为 popup 首屏的主要视觉焦点
- 保持现有推荐、反馈、画像、聊天逻辑不变，仅刷新结构、层级与交互反馈；extension 测试、typecheck 和 build 均已通过

### 9.3 聊天学习链路 — `soul/m93-chat-learning`

- 聊天现在会落 `dialogue` 事件，并额外提取 `interest / dislike / goal / value / state` 类型的候选长期理解信号
- 新增 `insight_candidates.json` 作为中间状态，先累计聊天候选，再由阈值控制是否进入偏好层
- 只有高置信度且重复出现的聊天候选才会驱动偏好重分析，并在变化明显时重建画像
- CLI `chat` 与 popup “和阿B聊聊” 现在共用这条学习链，但仍保持受控更新，不会因为单轮对话立即改写画像

### 运行时 Cookie 回退修复 — `main`

- 修复 `auth login` 与运行时命令脱节的问题：`init`、浏览器集成和本地服务现在会优先使用显式配置 cookie，留空时自动回退到 `data/bilibili_cookie.json`
- 用户完成一次 `auth login` 后，不再需要把同一份 cookie 重复抄进 `config.toml`
- 新增认证测试，锁定显式 cookie 优先级和已保存 cookie 回退行为

### Popup 画像 / 聊天页签增强 — `extension/m84-popup-tabs`

- popup 新增 `推荐 / 我的画像 / 和阿B聊聊` 三个 tab，推荐不再是唯一入口
- 新增 `/api/profile-summary` 和 `/api/chat`，popup 可直接查看轻量画像摘要并发起对话
- 推荐卡片交互已收口为显式打开视频，不再因为 `喜欢 / 不喜欢 / 写一句` 或输入框点击误跳转
- popup 内的推荐反馈、画像查看和聊天现在共用同一套本地后端连接状态

### 9.2 画像更新 — `feedback/m92-profile-refresh`

- 新增 `feedback_state.json`，记录反馈重分析处理游标和最近一次处理时间
- 反馈累计达到阈值后，会自动触发偏好层重新分析
- 当高权重兴趣或不喜欢主题变化明显时，会自动重建并持久化 `soul.json`
- CLI `feedback` 与 API `/api/feedback` 在反馈成功后都会同步触发这条更新链

### 9.1 反馈处理 — `feedback/m91-processing`

- CLI `feedback` 命令扩展为支持 `like / dislike / comment`，其中 `comment` 必须带 `--note`
- 新增 `POST /api/feedback`，统一校验推荐存在性、更新反馈字段并追加 `feedback` 事件
- popup 的 `喜欢 / 不喜欢 / 写一句` 已接通真实后端，提交后会立即写回推荐记录
- `9.1` 的反馈写入链路现已在 CLI、API、popup 三端统一

### 8.3 Popup — `extension/m83-popup`

- popup 从占位页升级为真实面板：显示后端连接状态和最新推荐列表
- 新增 popup helper，统一处理推荐字段 fallback、popup 状态判断和 B 站视频 URL 构造
- 点击推荐卡片或“打开视频”按钮会直接跳转到对应 B 站视频页
- `喜欢 / 不喜欢` 按钮本轮先保留 UI 占位，后端反馈写回留给后续任务

### 8.1 行为采集 — `extension/m81-behavior-collection`

- `collector.ts` 从最小 click/search 采集升级为多行为采集：点击、搜索、页面快照、视频 `view/pause/seek`、hover、scroll，以及评论/点赞/投币/收藏意图事件
- 补齐 SPA 导航感知：包装 `history.pushState` / `replaceState` 并监听 `popstate`，在 URL 变化时重新发送 `snapshot` 并重绑页面监听
- 新增纯逻辑 helper 和 Node 内置测试，覆盖页面识别、BV 提取、动作识别、缓冲去重与强信号 flush 判断
- `service-worker.ts` 改为带去重和失败回填的缓冲发送器，并使用 `chrome.alarms` 代替脆弱的 `setInterval`
- 新增 `extension/package.json`，提供 `npm test`、`npm run typecheck`、`npm run build`，让插件侧具备最小可验证构建链路
- 联调修复：补齐 manifest 图标资源，并把运行时脚本改为 `esbuild` bundle 单文件，解决 Chrome content script / service worker 的真实加载失败

### 8.2 后端 API — `api/m82-backend-api`

- 新增 FastAPI 应用，提供 `GET /api/health`、`POST /api/events`、`GET /api/recommendations`
- 插件上报的行为事件会映射到记忆系统事件层，并写入 SQLite `events` 表
- 推荐接口会返回推荐 ID、BV 号、标题、UP 主、推荐文案与展示状态，供插件 popup 使用
- CLI `openbiliclaw start` 从 stub 升级为真实本地 API 服务启动入口，默认监听 `127.0.0.1:8420`
- 联调修复：API 现已支持 extension 预检请求（CORS），并把 `/api/events` 改为 async 处理，避免 SQLite 线程错误

## M5: 内容发现引擎（进行中）

## M7: CLI 体验 ✅

### 7.1 chat 命令补平 — `cli/m71-chat-command`

- `openbiliclaw chat` 从 stub 升级为交互式 REPL，对接 `SocraticDialogue`
- 支持多轮对话，输入 `exit` / `quit` / 空行即可正常结束
- 新增 CLI 测试，覆盖画像缺失、单轮回复和退出路径

### 7.1 discover 命令补平 — `cli/m71-discover-command`

- `openbiliclaw discover` 从 stub 升级为真实命令：读取画像、执行 discovery engine、展示发现摘要与前 5 条预览
- 发现结果继续由 `ContentDiscoveryEngine` 写入 `content_cache`，CLI 只负责编排和展示
- 新增 CLI 测试，覆盖画像缺失、空发现结果和成功预览三条主路径

### 7.2 输出格式 — `cli/m72-output-format`

- `cli.py` 抽出统一 Rich 渲染 helper：页面标题、状态面板、键值表、占位态、推荐卡片
- `init` / `profile` / `recommend` / `feedback` / `config-show` / `auth status` / `health-check` / `browser` 命令全部切到统一展示风格
- `start` / `discover` / `chat` 的 stub 输出统一成“开发中”占位态，并附下一步提示
- CLI 测试补充输出结构断言，覆盖画像分区、推荐卡片、初始化摘要和状态面板语义

### 5.6 发现引擎编排 — `discovery/m56-engine-orchestration`

- `ContentDiscoveryEngine.discover()` 改为并发执行多个 discovery strategy，单个策略失败不会中断整体发现周期
- 引擎层对重复 `bvid` 进行合并，保留更高 `relevance_score` 的版本
- 新增 `Database.get_cached_content()`，并在发现完成后把最终结果写入 `content_cache`
- `evaluate_content()` 状态同步收口到 `5.5`：已被 Search / Trending / RelatedChain / Explore 复用
- 新增 discovery/storage 测试，覆盖并发编排、失败容错、高分去重和缓存写入读回

### 5.4 跨领域探索策略 — `discovery/m54-explore-strategy`

- `ExploreStrategy` 从空壳升级为可运行策略：先生成“高相关但有陌生感”的探索领域，再调用 B 站搜索
- 新增结构化 exploration prompt，要求输出 `domain` / `why_it_might_resonate` / `novelty_level` / `queries`
- 本地过滤与现有高权重兴趣过近的领域，避免“换皮搜索”
- 搜索候选统一复用 `ContentDiscoveryEngine.evaluate_content()`，并叠加基于 `novelty_level` 与 `exploration_openness` 的 exploration bonus
- 新增 explore 测试，覆盖领域过滤、bonus、生效阈值、部分失败容错和 engine 注册运行

### 5.3 相关推荐链策略 — `discovery/m53-related-chain`

- `RelatedChainStrategy` 从空壳升级为可运行策略：优先从事件层中的 `view` / `favorite` / `like` 视频挑选种子
- 种子不足时，先用偏好标签和常看 UP 主做小范围搜索补种子，再回退到 Search/Trending 的高分结果
- 对每个种子调用 `get_related_videos()`，沿相关推荐链最多扩展 2 层，并全局按 `bvid` 去重
- 统一复用 `ContentDiscoveryEngine.evaluate_content()` 对相关推荐候选打分，并按阈值过滤
- 新增 related-chain 测试，覆盖事件种子优先、fallback、二层扩展、去重、失败容错和 engine 注册运行

### 5.2 排行榜策略 — `discovery/m52-trending-strategy`

- `TrendingStrategy` 从空壳升级为可运行策略：拉取全站榜 `rid=0` 和相关分区榜，并按 `bvid` 去重
- 新增结构化分区选择 prompt，统一通过 `LLMService.complete_structured_task()` 选择额外 `rid`
- `ContentDiscoveryEngine.evaluate_content()` 现已实现：用 LLM 输出 `score/reason` 并写回 `DiscoveredContent`
- `TrendingStrategy` 对每条榜单内容执行相关性评估，只保留高于阈值的结果
- 新增 discovery 层测试，覆盖分区选择、阈值过滤、单榜单失败不中断和内容评估写回

### 5.1 搜索策略 — `discovery/m51-search-strategy`

- `SearchStrategy` 从空壳升级为可运行策略：基于画像生成搜索词、调用 B 站搜索并返回 `DiscoveredContent`
- 新增结构化搜索 query prompt，统一通过 `LLMService.complete_structured_task()` 生成 5 到 10 个 B 站搜索词
- 增加本地 fallback query 生成：当 LLM 返回坏 JSON 或空结果时，从兴趣标签和核心特质回退
- 对跨 query 搜索结果按 `bvid` 去重，并映射 `title` / `up_name` / `cover_url` / `duration` / `view_count` / `description`
- 新增 discovery 层测试，覆盖 query 生成、fallback、单 query 失败不中断和 engine 注册运行

## M4: 记忆系统（进行中）

### 4.5 核心记忆加载 — `memory/m45-core-memory`

- `MemoryManager.get_core_memory()` 从原始层数据改为稳定裁剪摘要，统一输出 `soul_summary` / `preference_summary` / `recent_awareness` / `active_insights`
- `MemoryManager.render_core_memory_prompt()` 改为固定区块渲染：用户画像、偏好摘要、近期观察、当前洞察
- `LLMService` 新增 `complete_with_core_memory()` / `complete_structured_task()`，统一自动注入 core memory
- `ProfileBuilder`、`PreferenceAnalyzer`、`AwarenessAnalyzer`、`InsightAnalyzer` 运行时全部改走统一 service 注入路径
- `SoulEngine` 现在内置 `LLMService`，保证画像、偏好、觉察、洞察链路都能共享同一份核心记忆上下文
- 后续收口修复已移除上述 4 个模块对原始 `registry.complete(..., json_mode=True)` 的 fallback，core memory 注入现在是强约束而非默认路径

### 4.4 觉察层与洞察层 — `memory/m44-awareness-insight`

- 新增 `AwarenessAnalyzer`：近期事件 -> `AwarenessNote`，支持坏 JSON 保护和同日去重
- 新增 `InsightAnalyzer`：觉察 + 偏好 + 画像 -> `InsightHypothesis`，支持假设合并与证据去重
- `SoulEngine.generate_awareness_note()` / `generate_insight()` 对接 analyzer，并持久化到 `awareness.json` / `insight.json`
- `SoulEngine.update_from_feedback()` 现在会写入 `feedback` 事件，并更新匹配洞察的 `validated` / `confidence`

### 4.3 灵魂层 — `memory/m43-soul-layer`

- 新增 `ProfileBuilder`：结构化画像 prompt、JSON 校验和 `SoulProfile` 构建
- `SoulEngine.build_initial_profile()` 从 history + preference 生成初始画像并持久化到 `data/memory/soul.json`
- `SoulEngine.get_profile()` 支持读取已保存画像，未初始化时抛 `SoulProfileNotInitializedError`
- `SoulProfile` 增加 `to_dict()` / `from_dict()` 及偏好层序列化辅助
- CLI `profile` 命令从 stub 升级为真实展示，缺失画像时提示后续执行 `openbiliclaw init`

### 4.2 偏好层 — `memory/m42-preference-layer`

- 新增 `PreferenceAnalyzer`：LLM structured extraction + JSON 解析 + 兴趣合并
- 新增 `build_preference_analysis_prompt()`：结构化偏好提取 prompt
- `SoulEngine.analyze_events()` 对接 `PreferenceAnalyzer`，偏好持久化到 JSON
- 兴趣标签带时间衰减（`decay_factor_per_week=0.9`）和最低权重过滤

### 4.1 事件层 — `memory/m41-event-layer`

- `Database` 新增 `query_events()` 和 `count_events_by_type()`
- `MemoryManager.propagate_event()` 从 stub 改为 SQLite 持久化
- 事件类型枚举：`view`, `search`, `favorite`, `like`, `comment`, `click`, `feedback`
- 新增 `MemoryManager.query_events()` 和 `get_event_stats()` 委托方法

---

## M6: 推荐引擎（进行中）

### 6.3 推荐持久化 — `recommendation/m63-persistence`

- `recommendations` 表补齐结构化反馈字段：`feedback_type`、`feedback_note`、`feedback_at`
- 新增 `Database.get_recommendation_by_id()` 和 `update_recommendation_feedback()`，支持推荐反馈读写
- `RecommendationEngine` 新增 `record_feedback()` / `get_recommendation()` 入口
- CLI 新增 `feedback <id> <like|dislike> [--note ...]`，成功后会同步写入一条 `feedback` 事件
- 新增 recommendation/storage/cli 测试，覆盖反馈持久化、事件写入和不存在推荐的错误路径

## M7: CLI 交付（进行中）

### 7.1 核心命令 `init` — `cli/m71-init`

- 新增 `openbiliclaw init`，打通首次运行链路：认证检查、历史拉取、事件导入、偏好分析、画像生成、自动 discover
- 新增 `_build_bilibili_client()`、`_build_discovery_engine()` 和 `_history_item_to_event()`，把 CLI 编排边界固定下来
- `init` 支持阶段性进度输出，并在 discover 失败时给出“部分完成”提示，不丢弃已生成的画像
- 新增 CLI 测试，覆盖认证失败、历史为空、全流程成功和 discover 部分失败

### 6.2 朋友式推荐表达 — `recommendation/m62-expression`

- `RecommendationEngine.generate_expression()` 从 stub 升级为结构化 LLM 调用，输出 `expression` 和 `topic_label`
- `generate_recommendations()` 现在会为每条推荐补全朋友式文案，并回写到 `recommendations` 表
- 新增 `Database.update_recommendation_content()` 和 `mark_recommendations_presented()`，打通推荐文案更新与展示状态更新
- CLI `recommend` 从 stub 升级为真实展示入口，会读取用户画像、生成推荐并在输出后标记已展示
- 新增 recommendation/storage/cli 测试，覆盖文案生成、推荐历史回写和展示后状态更新

### 6.1 推荐排序 — `recommendation/m61-ranking`

- `RecommendationEngine.generate_recommendations()` 从 stub 升级为可运行排序入口
- 支持两种来源：显式传入 `discovered`，或直接从 `content_cache` 读取未推荐内容
- 新增 `Database.get_unrecommended_content()`、`insert_recommendation()`、`get_recommendations()`
- 每次生成推荐后，立即写入最小推荐历史记录，避免下一批重复选中同一内容
- 新增 recommendation/storage 测试，覆盖排序、缓存读取和去重闭环

## M3: Bilibili 接入层 ✅

### 3.3 agent-browser 集成 — `bili/m33-agent-browser`

- `BilibiliBrowser` 重写：`BrowserCommandError` 异常 + `open` → `snapshot -i --json` 流程
- CLI 新增 `browser status` / `browser open` / `browser content` 命令
- `is_available` 检测 + 官方安装提示

### 3.2 核心 API — `bili/m32-core-api`

- `BilibiliAPIClient` 新增统一请求助手 `_get_json()` + 轻量限流 `_respect_rate_limit()`
- 新增 cursor-based `get_user_history(max_items=200)`
- 新增 `get_favorite_folders()` / `get_all_favorites()` 带预算控制
- 新增 `get_following()` / `get_video_comments()`
- 新增 `FavoriteFolder`, `FavoriteFolderWithItems`, `FollowingUser`, `CommentInfo` 数据结构
- 新增集成测试骨架 `@pytest.mark.integration`

### 3.1 Cookie 认证 — `bili/m31-cookie-auth`

- `AuthManager`：cookie 持久化 + nav API 验证 + `SupportsNavClient` Protocol DI
- `BilibiliAPIClient.get_nav_info()`：解析 `/x/web-interface/nav`
- CLI 新增 `auth login`（交互式 + `--cookie`）和 `auth status`

---

## M2: LLM 多模型支持 ✅

### 2.3 Prompt 管理与 LLM Service — `llm/m23-prompt-management`

- 新增 `prompts.py`：Socratic 对话 prompt 构建 + core memory 注入
- 新增 `service.py`：`LLMService` 门面（prompt 组装 + registry 调用 + 空响应校验）
- 新增 `MemoryManager.render_core_memory_prompt()`
- `SocraticDialogue.respond()` 对接 LLMService，替换 TODO stub

### 2.2 Provider Registry — `llm/m22-registry`

- 新增 `build_llm_registry()`：从 Config 自动构建 + provider fallback
- `LLMRegistry.complete()`：sequential fallback，`LLMResponseError` 不触发 fallback
- CLI 新增 `health-check` 命令 + `config-show` 显示已注册 provider

### 2.1 Provider 实现 — `llm/m21-providers`

- 新增统一异常层级：`LLMProviderError` → `LLMRateLimitError` / `LLMTimeoutError` / `LLMResponseError`
- `OpenAIProvider` / `ClaudeProvider`：retry + 超时映射 + 空响应保护
- 新增 `OllamaProvider`（本地 LLM）
- 新增 `DeepSeekProvider`（继承 OpenAI）

---

## M1: 基础设施 ✅

### 1.3 日志系统 — `infra/m13-logging-system`

- 新增 `logging_setup.py`：Rich 控制台 + 文件 handler，防重复初始化
- `LoggingConfig`：level / file_level / directory / filename
- CLI 全局 `--log-level` 选项

### 1.2 配置系统 — `infra/m12-config-system`

- `config.py` 增强：`ConfigError` / `ConfigDiagnostics` / 严格校验
- CLI `config-show` 显示配置 + 引导提示
- `config.example.toml` 完整注释

### 1.1 开发环境和 CI — `infra-m1`

- Ruff + MyPy + Pytest 质量门禁
- GitHub Actions CI 工作流
- `tomllib` 配置加载
